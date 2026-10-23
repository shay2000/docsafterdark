"""Read-only, live Google Docs check using the packaged extension."""
import json
import pathlib
import tempfile
from PIL import Image, ImageChops, ImageStat
from playwright.sync_api import sync_playwright
ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / 'test-results'
OUT.mkdir(exist_ok=True)
URL = 'https://docs.google.com/document/d/1g9BCQaWd6FnIQnfOozVDfG9ySi7xZZxD6IiwelvC3w4/edit'
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    baseline = browser.new_page(viewport={'width': 1280, 'height': 1100},device_scale_factor=1)
    baseline.goto(URL,wait_until='domcontentloaded')
    canvas=baseline.locator('canvas.kix-canvas-tile-content').first
    canvas.wait_for(timeout=60000)
    baseline.wait_for_timeout(7000)
    canvas.screenshot(path=str(OUT/'live-original.png'))
    browser.close()
    with tempfile.TemporaryDirectory(prefix='dad-extension-') as profile:
        context = p.chromium.launch_persistent_context(profile,channel='chromium',headless=True,
            args=[f'--disable-extensions-except={ROOT / "build"}',f'--load-extension={ROOT / "build"}'],
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
        difference=ImageStat.Stat(ImageChops.difference(a.crop(region),b.crop(region))).mean
        result={'title':page.title(),'image_channel_mean_error':difference,
                'dark_page_pixel':b.getpixel((600,500)),
                'hook_active':canvas.get_attribute('data-dad-image-safe') is not None,
                'browser':context.browser.version,'page_errors':errors}
        print(json.dumps(result,indent=2))
        (OUT/'live-result.json').write_text(json.dumps(result,indent=2))
        assert max(difference)<2, f'Live image colours changed: {difference}'
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
            diff=ImageStat.Stat(ImageChops.difference(a.crop(region),shot.crop(region))).mean
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
        print('PASS: popup Off/Dark, live Auto light/dark, highlight toggle, quick off/on, image colours unchanged')
        context.close()
        print('PASS: real packaged extension preserves live image colours and readable text')
