"""Read-only, live Google Docs check using the packaged extension."""
import json
import pathlib
import tempfile
import argparse
import io
from PIL import Image, ImageChops, ImageStat
from playwright.sync_api import sync_playwright
ROOT = pathlib.Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--out', type=pathlib.Path, default=ROOT / 'test-results')
parser.add_argument('--build', type=pathlib.Path, default=ROOT / 'build')
parser.add_argument('--cycles', type=int, default=1)
parser.add_argument('--allow-legacy-fallback', action='store_true',
                    help='Explicitly test canonical legacy rendering; not a photo-preservation pass')
args = parser.parse_args()
OUT = args.out
OUT.mkdir(exist_ok=True, parents=True)
URL = 'https://docs.google.com/document/d/1g9BCQaWd6FnIQnfOozVDfG9ySi7xZZxD6IiwelvC3w4/edit'
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    baseline = browser.new_page(viewport={'width': 1280, 'height': 1100},device_scale_factor=1)
    baseline.goto(URL,wait_until='domcontentloaded')
    canvas=baseline.locator('canvas.kix-canvas-tile-content').first
    canvas.wait_for(timeout=60000)
    baseline.wait_for_timeout(7000)
    canvas.screenshot(path=str(OUT/'live-original.png'))
    baseline_canvas = canvas
    with tempfile.TemporaryDirectory(prefix='dad-extension-') as profile:
        context = p.chromium.launch_persistent_context(profile,channel='chromium',headless=True,
            args=[f'--disable-extensions-except={args.build}',f'--load-extension={args.build}'],
            ignore_default_args=['--disable-extensions'], viewport={'width':1280,'height':1100},device_scale_factor=1)
        page=context.new_page()
        errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(URL,wait_until='domcontentloaded')
        canvas=page.locator('canvas.kix-canvas-tile-content').first
        canvas.wait_for(timeout=60000)
        page.wait_for_timeout(7000)
        assert page.evaluate("document.documentElement.classList.contains('DocsAfterDark_enabled')"), 'Packaged extension did not activate'
        canvas.screenshot(path=str(OUT/'live-fixed.png'))
        page.screenshot(path=str(OUT/'live-fixed-page.png'))
        a=Image.open(OUT/'live-original.png').convert('RGB')
        b=Image.open(OUT/'live-fixed.png').convert('RGB')
        # Inside the opaque image, away from its edges and text.
        region=(120,150,340,290)
        assert max(ImageStat.Stat(a.crop(region)).stddev)>10, 'Blank baseline photo crop'
        references={'none':a}
        def renderer():
            safe=canvas.get_attribute('data-dad-image-safe') is not None
            fallback=canvas.get_attribute('data-dad-canvas-fallback')
            assert safe or fallback, 'MAIN-world renderer did not activate'
            assert safe or args.allow_legacy_fallback, 'Legacy fallback is not an image-safe photo pass: '+str(fallback)
            return safe,fallback
        def reference():
            safe,fallback=renderer()
            effective='none' if safe else canvas.evaluate('(c)=>getComputedStyle(c).filter')
            if effective not in references:
                baseline_canvas.evaluate('(c,f)=>{c.style.filter=f}',effective)
                references[effective]=Image.open(io.BytesIO(baseline_canvas.screenshot())).convert('RGB')
            return references[effective]
        difference=ImageStat.Stat(ImageChops.difference(a.crop(region),b.crop(region))).mean
        safe,fallback=renderer()
        reference_difference=ImageStat.Stat(ImageChops.difference(reference().crop(region),b.crop(region))).mean
        result={'title':page.title(),'image_channel_mean_error':difference,
                'dark_page_pixel':b.getpixel((600,500)),
                'hook_active':canvas.get_attribute('data-dad-image-safe') is not None,
                'legacy_fallback':fallback,'reference_mean_error':reference_difference,
                'browser':context.browser.version,'page_errors':errors}

        print(json.dumps(result,indent=2))
        (OUT/'live-result.json').write_text(json.dumps(result,indent=2))
        assert max(reference_difference)<2, f'Live renderer differs from its explicit native reference: {reference_difference}'
        assert max(b.getpixel((600,500)))<30, 'Live page is not dark'
        # Black original label must become bright, not stay black-on-grey.
        pixels=list(b.crop((100,95,320,120)).get_flattened_data())
        assert sum(min(rgb)>200 for rgb in pixels)>80, 'Live label did not become readable'
        assert not errors, 'Page JavaScript errors occurred'
        popup=context.new_page()
        extension_url=page.locator('link[href^="chrome-extension://"]').first.get_attribute('href').rsplit('/',1)[0]
        popup.goto(extension_url+'/popup.html')
        def mode(button, expected_dark, label):
            popup.locator(button).click()
            page.wait_for_timeout(500)
            canvas.screenshot(path=str(OUT/(label+'.png')))
            shot=Image.open(OUT/(label+'.png')).convert('RGB')
            assert (max(shot.getpixel((600,500)))<30)==expected_dark, label+' page brightness incorrect'
            diff=ImageStat.Stat(ImageChops.difference(reference().crop(region),shot.crop(region))).mean
            assert max(diff)<2, label+' changed the opaque image colours'
        mode('#modeOff',False,'live-off')
        mode('#modeDark',True,'live-dark-restored')
        page.emulate_media(color_scheme='light')
        mode('#modeAuto',False,'live-auto-light')
        page.emulate_media(color_scheme='dark')
        page.wait_for_timeout(500)
        canvas.screenshot(path=str(OUT/'live-auto-dark.png'))
        auto=Image.open(OUT/'live-auto-dark.png').convert('RGB')
        assert max(auto.getpixel((600,500)))<30, 'Auto did not follow dark preference'
        mode('#modeDark',True,'live-final-dark')
        popup.locator('label[for="highlightVisibility"]').click()
        page.wait_for_timeout(500)
        canvas.screenshot(path=str(OUT/'live-highlight-off.png'))
        low=Image.open(OUT/'live-highlight-off.png').convert('RGB').getpixel((110,625))
        popup.locator('label[for="highlightVisibility"]').click()
        page.wait_for_timeout(500)
        canvas.screenshot(path=str(OUT/'live-highlight-on.png'))
        high=Image.open(OUT/'live-highlight-on.png').convert('RGB').getpixel((110,625))
        assert sum(high)>sum(low), f'Highlight setting did not brighten highlights: {low} -> {high}'
        page.locator('#DocsAfterDark_button').click()
        page.wait_for_timeout(300)
        canvas.screenshot(path=str(OUT/'live-quick-off.png'))
        assert min(Image.open(OUT/'live-quick-off.png').convert('RGB').getpixel((600,500)))>245, 'Quick toggle did not restore original page'
        page.locator('#DocsAfterDark_button').click()
        page.wait_for_timeout(300)
        canvas.screenshot(path=str(OUT/'live-quick-on.png'))
        assert max(Image.open(OUT/'live-quick-on.png').convert('RGB').getpixel((600,500)))<30, 'Quick toggle did not restore dark page'
        assert not errors, f'Page errors after settings changes: {errors}'
        # Repeat real controls, checking every rendered image crop, not merely
        # a final state. These settings are isolated in the disposable profile.
        transitions=[]
        def check(label, dark):
            page.wait_for_timeout(150)
            path=OUT/(label+'.png')
            canvas.screenshot(path=str(path))
            shot=Image.open(path).convert('RGB')
            diff=ImageStat.Stat(ImageChops.difference(reference().crop(region),shot.crop(region))).mean
            background=shot.getpixel((600,500))
            assert isinstance(background,tuple)
            hooked,fallback=renderer()
            transitions.append({'label':label,'dark':dark,'image_channel_mean_error':diff,
                                'background':background,'hook_active':hooked,'legacy_fallback':fallback,
                                'original_image_mean_error':ImageStat.Stat(ImageChops.difference(a.crop(region),shot.crop(region))).mean})
            assert max(diff)<2, label+' changed image colours'
            assert (max(background)<30)==dark, label+' brightness mismatch'
            assert hooked or fallback, label+' lost renderer activation'
        page.evaluate("window.dadMediaEvents=0;matchMedia('(prefers-color-scheme: dark)').addEventListener('change',()=>window.dadMediaEvents++)")
        for cycle in range(args.cycles):
            prefix=f'cycle-{cycle:02d}-'
            popup.locator('#modeOff').click();check(prefix+'off',False)
            popup.locator('#modeDark').click();check(prefix+'dark',True)
            page.emulate_media(color_scheme='light')
            popup.locator('#modeAuto').click();check(prefix+'auto-light',False)
            page.emulate_media(color_scheme='dark');check(prefix+'auto-dark',True)
            popup.locator('#modeDark').click()
            popup.locator('label[for="highlightVisibility"]').click();check(prefix+'highlight-off',True)
            popup.locator('label[for="highlightVisibility"]').click();check(prefix+'highlight-on',True)
            page.locator('#DocsAfterDark_button').click();check(prefix+'quick-off',False)
            page.locator('#DocsAfterDark_button').click();check(prefix+'quick-on',True)
        result['transitions']=transitions
        result['media_change_events']=page.evaluate('window.dadMediaEvents')
        result['cycles']=args.cycles
        result['page_errors']=errors
        (OUT/'live-result.json').write_text(json.dumps(result,indent=2)+'\n')
        assert result['media_change_events']>0, 'Live media listener was not exercised'
        assert len(transitions)==args.cycles*8
        assert not errors, errors
        print('PASS: popup Off/Dark, live Auto light/dark, highlight toggle, quick off/on against explicit native rendering references')
        print(f'PASS: {len(transitions)} repeated rendered transitions; {result["media_change_events"]} actual media change events')
        context.close()
        browser.close()
        print('PASS: packaged extension controls and canonical rendering; inspect fallback/error fields before claiming image preservation')
