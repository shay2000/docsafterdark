"""Real Chromium pixel regression; no Google account or document edits."""
import json
import pathlib
import subprocess
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / 'test-results'
OUT.mkdir(exist_ok=True)
source = ROOT / 'src/image-safe-canvas.ts'
script = subprocess.check_output([
    'node', '--input-type=module', '-e',
    "import ts from 'typescript'; import fs from 'node:fs'; const p=process.argv[1]; console.log(fs.existsSync(p)?ts.transpile(fs.readFileSync(p,'utf8'),{target:ts.ScriptTarget.ES2022}):'');",
    str(source)
], cwd=ROOT, text=True)
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 700, 'height': 320})
    errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.set_content('''<style>
      body {background:#777} canvas {filter:invert(1) hue-rotate(180deg) brightness(1.8) saturate(1.35)}
      canvas[data-dad-image-safe] {filter:none}
    </style><canvas class="kix-canvas-tile-content" width="600" height="240"></canvas>''')
    page.evaluate(script)
    page.evaluate('''() => {
      document.documentElement.setAttribute('data-dad-canvas-filter','invert(1) hue-rotate(180deg) brightness(1.8) saturate(1.35)');
    }''')
    page.wait_for_timeout(100)
    page.evaluate('''() => {
      const c=document.querySelector('canvas'), x=c.getContext('2d');
      x.fillStyle='white';x.fillRect(0,0,600,240);
      x.fillStyle='black';x.font='bold 32px sans-serif';x.fillText('Readable text',20,50);
      x.fillStyle='yellow';x.fillRect(20,75,240,40);
      x.fillStyle='black';x.fillText('Highlight',25,106);
      const im=document.createElement('canvas');im.width=100;im.height=100;
      const ix=im.getContext('2d');ix.fillStyle='#e04020';ix.fillRect(0,0,100,100);
      x.drawImage(im,350,20);
      window.ctx=x;window.originalStyle=x.fillStyle;
    }''')
    page.screenshot(path=str(OUT/'fixture-dark.png'))
    from PIL import Image
    def pixel(x,y):
        return Image.open(OUT/'current.png').convert('RGB').getpixel((x+8,y+8))
    def snap():page.screenshot(path=str(OUT/'current.png'))
    snap()
    actual=pixel(390,50)
    assert max(abs(a-b) for a,b in zip(actual,(224,64,32))) <= 2, f'Image colours changed: {actual}, expected (224,64,32)'
    assert max(pixel(280,180))<15, f'Page is not dark: {pixel(280,180)}'
    white=page.evaluate("() => {const d=ctx.getImageData(20,20,240,40).data;return Array.from(d).filter((v,i)=>i%4!==3 && v>220).length}")
    assert white>300, 'Black text was not made readable'
    assert page.evaluate('ctx.fillStyle===window.originalStyle'), 'Drawing state leaked'
    page.evaluate("document.documentElement.setAttribute('data-dad-canvas-filter','none')")
    page.wait_for_timeout(100)
    snap()
    # Disable the legacy fixture filter just as the extension removes its stylesheet.
    page.add_style_tag(content='canvas {filter:none}')
    snap()
    assert min(pixel(280,180))>245, 'Turning inversion off did not restore the existing canvas'
    assert max(abs(a-b) for a,b in zip(pixel(390,50),(224,64,32)))<=2
    page.screenshot(path=str(OUT/'fixture-off.png'))
    page.evaluate("document.documentElement.setAttribute('data-dad-canvas-filter','invert(1)')")
    page.wait_for_timeout(100)
    snap()
    assert max(pixel(280,180))<15, 'Turning inversion back on did not replay the page'
    assert max(abs(a-b) for a,b in zip(pixel(390,50),(224,64,32)))<=2
    # A failed native draw must not poison subsequent theme-change replays.
    page.evaluate('''() => { try {ctx.drawImage(null,0,0)} catch (_) {} }''')
    page.evaluate("document.documentElement.setAttribute('data-dad-canvas-filter','none')")
    page.wait_for_timeout(100)
    snap()
    assert min(pixel(280,180))>245, 'Invalid draw poisoned the command replay'
    page.evaluate("document.documentElement.setAttribute('data-dad-canvas-filter','invert(1)')")
    page.wait_for_timeout(100)
    snap()
    assert max(pixel(280,180))<15, 'Canvas did not recover after invalid draw'
    edge=page.evaluate('''() => {
      const c=document.querySelector('canvas');c.width=600;
      ctx.fillStyle='white';ctx.fillRect(0,0,600,240);
      const s=document.createElement('canvas');s.width=20;s.height=20;
      const z=s.getContext('2d');z.fillStyle='#e04020';z.fillRect(0,0,10,20);
      ctx.save();ctx.translate(40,20);ctx.drawImage(s,0,0);ctx.restore();
      z.fillStyle='green';z.fillRect(0,0,20,20);
      const other=document.createElement('canvas');other.width=20;other.height=20;
      const o=other.getContext('2d');o.fillStyle='red';o.fillRect(0,0,20,20);
      return Array.from(o.getImageData(5,5,1,1).data);
    }''')
    assert edge==[255,0,0,255], 'Unrelated canvas was modified'
    page.evaluate("document.documentElement.setAttribute('data-dad-canvas-filter','none')")
    page.wait_for_timeout(100)
    assert page.evaluate('Array.from(ctx.getImageData(45,25,1,1).data)')==[224,64,32,255], 'Mutable image source or saved transform replay changed'
    assert page.evaluate('Array.from(ctx.getImageData(55,25,1,1).data)')==[255,255,255,255], 'Transparent image area was filled'
    page.evaluate('''() => {
      for(let i=0;i<30010;i++) ctx.fillRect(0,0,1,1);
    }''')
    assert page.locator('canvas').first.get_attribute('data-dad-image-safe') is None, 'Command cap did not fall back'
    page.evaluate("document.querySelector('canvas').width=600;ctx.fillStyle='white';ctx.fillRect(0,0,600,240)")
    assert page.locator('canvas').first.get_attribute('data-dad-image-safe') is not None, 'Resize did not recover image-safe mode'
    assert not errors, f'Canvas wrapper introduced page errors: {errors}'
    print(json.dumps({'result':'PASS','checks':['image RGB preserved','dark page','readable text','drawing state restored','off restores existing canvas','on redraws existing canvas'],'browser':browser.version},indent=2))
    browser.close()
