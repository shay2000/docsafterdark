"""Read-only packaged-extension test on Firefox via standard WebDriver.

No Selenium dependency; pass an official geckodriver executable. Both sessions
use geckodriver's disposable profiles; only extension-local settings are changed.
"""
import argparse
import base64
import json
import pathlib
import socket
import subprocess
import time
import urllib.error
import urllib.request
from PIL import Image, ImageChops, ImageStat

ROOT = pathlib.Path(__file__).resolve().parents[1]
URL = 'https://docs.google.com/document/d/1g9BCQaWd6FnIQnfOozVDfG9ySi7xZZxD6IiwelvC3w4/edit'
parser = argparse.ArgumentParser()
parser.add_argument('--geckodriver', required=True)
parser.add_argument('--binary', default='/usr/bin/firefox')
PACKAGE_VERSION = json.loads((ROOT/'package.json').read_text())['version']
parser.add_argument('--package', type=pathlib.Path, default=ROOT/f'release/DocsAfterDark_{PACKAGE_VERSION}.zip')
parser.add_argument('--out', type=pathlib.Path, default=ROOT/'test-results/firefox-live')
parser.add_argument('--cycles', type=int, default=3)
args = parser.parse_args()
args.out.mkdir(parents=True, exist_ok=True)
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
log = (args.out/'geckodriver.log').open('w')
driver = subprocess.Popen([args.geckodriver, '--port', str(port), '--host', '127.0.0.1'], stdout=log, stderr=subprocess.STDOUT)
base = f'http://127.0.0.1:{port}'
session = None

def request(method, path, payload=None):
    body = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(base+path, data=body, method=method, headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            return json.load(response)['value']
    except urllib.error.HTTPError as error:
        raise RuntimeError(error.read().decode()) from error

def command(method, path, payload=None):
    assert session is not None, 'No active WebDriver session'
    return request(method, '/session/'+session+path, payload)

def js(script):
    return command('POST', '/execute/sync', {'script':'return ('+script+')', 'args':[]})

def wait(test, timeout=60):
    deadline = time.monotonic()+timeout
    while time.monotonic()<deadline:
        try:
            value = test()
        except urllib.error.URLError:
            value = None
        if value:
            return value
        time.sleep(.2)
    raise TimeoutError('Firefox page assertion timed out')

def create(extension=False):
    global session
    value = request('POST', '/session', {'capabilities': {'alwaysMatch': {
        'browserName':'firefox', 'moz:firefoxOptions':{'binary':args.binary, 'args':['-headless']}}}})
    session = value['sessionId']
    command('POST', '/window/rect', {'width':1280,'height':1100,'x':0,'y':0})
    # Align the content viewport, not the outer browser chrome.
    size=js('({width:outerWidth-innerWidth,height:outerHeight-innerHeight})')
    command('POST','/window/rect',{'width':1280+size['width'],'height':1100+size['height'],'x':0,'y':0})
    if extension:
        addon=command('POST','/moz/addon/install',{'path':str(args.package.resolve()),'temporary':True})
        assert addon=='{e8ffc3db-2875-4c7f-af38-d03e7f7f8ab9}', addon
    command('POST','/url',{'url':URL})
    wait(lambda: js("!!document.querySelector('canvas.kix-canvas-tile-content')"))
    time.sleep(7)
    return value['capabilities']

def element(selector):
    value=command('POST','/element',{'using':'css selector','value':selector})
    return value['element-6066-11e4-a52e-4f735466cecf']

def screenshot(name, full=False):
    route='/screenshot' if full else '/element/'+element('canvas.kix-canvas-tile-content')+'/screenshot'
    path=args.out/(name+'.png')
    path.write_bytes(base64.b64decode(command('GET',route)))
    return Image.open(path).convert('RGB')

def switch(handle):
    command('POST','/window',{'handle':handle})

def click(selector):
    command('POST','/element/'+element(selector)+'/click',{})

def close():
    global session
    if session:
        command('DELETE','')
        session=None

try:
    wait(lambda: request('GET','/status'), timeout=15)
    baseline_caps=create()
    original=screenshot('original')
    close()
    caps=create(extension=True)
    wait(lambda: js("document.documentElement.classList.contains('DocsAfterDark_enabled')"))
    support=js("({filter:'filter' in CanvasRenderingContext2D.prototype,reset:'reset' in CanvasRenderingContext2D.prototype})")
    document_handle=command('GET','/window')
    stylesheet=wait(lambda: js("document.querySelector('link[href^=\"moz-extension://\"]')?.href"))
    extension_url=stylesheet.rsplit('/',1)[0]
    fixed=screenshot('dark')
    screenshot('dark-page',full=True)
    region=(120,150,340,290)
    diff=ImageStat.Stat(ImageChops.difference(original.crop(region),fixed.crop(region))).mean
    assert max(diff)<2, f'Firefox image crop differs: {diff}'
    fixed_background=fixed.getpixel((600,500))
    assert isinstance(fixed_background,tuple)
    assert max(fixed_background)<30, 'Firefox page not dark'
    active=js("document.querySelector('canvas.kix-canvas-tile-content').hasAttribute('data-dad-image-safe')")
    assert active==all(support.values()), (active,support)
    assert js("!!document.querySelector('#DocsAfterDark_button')")
    initial={'browser':caps['browserVersion'],'binary':args.binary,'support':support,
             'hook_active':active,'extension_active':True,'image_channel_mean_error':diff,
             'stage':'initial-render-passed; popup controls not yet tested'}
    (args.out/'initial-result.json').write_text(json.dumps(initial,indent=2)+'\n')
    popup_handle=command('POST','/window/new',{'type':'tab'})['handle']
    switch(popup_handle)
    command('POST','/url',{'url':extension_url+'/popup.html'})
    wait(lambda:js("!!document.querySelector('#modeDark')"))
    transitions=[]
    def check(label,dark):
        switch(document_handle)
        time.sleep(.3)
        shot=screenshot(label)
        difference=ImageStat.Stat(ImageChops.difference(original.crop(region),shot.crop(region))).mean
        background=shot.getpixel((600,500))
        assert isinstance(background,tuple)
        transitions.append({'label':label,'image_channel_mean_error':difference,'background':background})
        assert max(difference)<2, label+' image mismatch'
        assert (max(background)<30)==dark, label+' background mismatch'
    for cycle in range(args.cycles):
        for button,dark,label in [('#modeOff',False,'off'),('#modeDark',True,'dark')]:
            switch(popup_handle);click(button);check(f'cycle-{cycle:02d}-'+label,dark)
        for label in ['highlight-off','highlight-on']:
            switch(popup_handle);click('label[for="highlightVisibility"]');check(f'cycle-{cycle:02d}-'+label,True)
        switch(document_handle);click('#DocsAfterDark_button');check(f'cycle-{cycle:02d}-quick-off',False)
        click('#DocsAfterDark_button');check(f'cycle-{cycle:02d}-quick-on',True)
    result={'result':'PASS','browser':caps['browserVersion'],'binary':args.binary,'support':support,
            'hook_active':active,'extension_active':True,'image_channel_mean_error':diff,
            'baseline_browser':baseline_caps['browserVersion'],'cycles':args.cycles,'transitions':transitions,
            'title':js('document.title'),'auto_tested':False,'startup_console_capture':False}
    assert len(transitions)==args.cycles*6
    (args.out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
finally:
    try:
        close()
    finally:
        driver.terminate()
        driver.wait(timeout=15)
        log.close()
