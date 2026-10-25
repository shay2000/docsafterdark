"""Real-browser regressions for replay safety; no network or document edits."""
import argparse
import json
import pathlib
import subprocess
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--source', type=pathlib.Path, default=ROOT/'src/image-safe-canvas.ts')
parser.add_argument('--browser', choices=['chromium', 'firefox'], default='chromium')
parser.add_argument('--case', choices=['resize', 'resources', 'blends', 'all'], default='all')
parser.add_argument('--out', type=pathlib.Path, default=ROOT/'test-results/regressions.json')
args = parser.parse_args()
script = subprocess.check_output([
    'node', '--input-type=module', '-e',
    "import ts from 'typescript';import fs from 'node:fs';console.log(ts.transpile(fs.readFileSync(process.argv[1],'utf8'),{target:ts.ScriptTarget.ES2022}));",
    str(args.source),
], cwd=ROOT, text=True)
with sync_playwright() as p:
    browser = getattr(p, args.browser).launch(headless=True)
    page = browser.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.set_content('<canvas class="kix-canvas-tile-content" width="64" height="64"></canvas>')
    page.evaluate(script)
    page.evaluate('''() => {
      window.c=document.querySelector('canvas');window.x=c.getContext('2d');
      window.active=()=>c.hasAttribute('data-dad-image-safe');
      window.pixel=()=>Array.from(x.getImageData(2,2,1,1).data);
      window.tick=async value=>{document.documentElement.setAttribute('data-dad-canvas-filter',value);
        await new Promise(resolve=>setTimeout(resolve,0));};
    }''')
    result = {'browser': browser.version, 'source': str(args.source), 'cases': {}}
    if args.case in ('resize', 'all'):
        result['cases']['resize'] = page.evaluate('''async () => {
          const checks=[];
          for(const resize of [()=>c.setAttribute('width','64'),()=>c.setAttributeNS(null,'height','64'),
              ()=>c.attributes.width.value='64',()=>{c.removeAttribute('width');c.width=64}]){
            c.width=64;x.fillStyle='red';x.fillRect(0,0,8,8);resize();
            // Draw synchronously before mutation-observer delivery: clearing the
            // log later must not discard this new drawing.
            x.fillStyle='blue';x.fillRect(20,20,4,4);
            await tick('invert(1)');await tick('none');
            checks.push({clear:pixel(),newDraw:Array.from(x.getImageData(21,21,1,1).data),active:active()});
          }
          // No intervening drawing before a resize and theme change.
          c.width=64;x.fillStyle='red';x.fillRect(0,0,8,8);
          c.setAttribute('width','64');await tick('invert(1)');await tick('none');
          checks.push({clear:pixel(),active:active()});
          // Detached tiles can still be resized and drawn before reattachment.
          c.remove();x.fillStyle='red';x.fillRect(0,0,8,8);c.setAttribute('width','64');
          x.fillStyle='blue';x.fillRect(20,20,4,4);document.body.append(c);
          await tick('invert(1)');await tick('none');
          checks.push({clear:pixel(),newDraw:Array.from(x.getImageData(21,21,1,1).data),active:active()});
          return checks;
        }''')
    if args.case in ('resources', 'all'):
        result['cases']['resources'] = page.evaluate('''async () => {
          const checks={};
          const probe=async(name,setup,mutate)=>{
            c.width=64;await tick('invert(1)');
            x.fillStyle='white';x.fillRect(0,0,64,64);setup();
            const fallback=!active();await tick('none');const before=pixel();mutate();
            await tick('invert(1)');await tick('none');
            checks[name]={fallback,marked:c.hasAttribute('data-dad-canvas-fallback'),before,after:pixel(),stable:JSON.stringify(before)===JSON.stringify(pixel())};
          };
          const data=new ImageData(8,8);data.data.fill(255);
          await probe('ImageData',()=>x.putImageData(data,0,0),()=>data.data.fill(0));
          const path=new Path2D();path.rect(20,20,4,4);
          await probe('Path2D',()=>{x.fillStyle='red';x.fill(path)},()=>path.rect(0,0,8,8));
          const gradient=x.createLinearGradient(0,0,8,0);gradient.addColorStop(0,'red');gradient.addColorStop(1,'red');
          await probe('gradient',()=>{x.fillStyle=gradient;x.fillRect(0,0,8,8)},()=>gradient.addColorStop(.5,'blue'));
          const source=document.createElement('canvas');source.width=source.height=8;
          const z=source.getContext('2d');z.fillStyle='red';z.fillRect(0,0,8,8);
          const pattern=x.createPattern(source,'repeat');
          await probe('pattern',()=>{x.fillStyle=pattern;x.fillRect(0,0,8,8)},()=>{z.fillStyle='blue';z.fillRect(0,0,8,8)});
          if(typeof OffscreenCanvas!=='undefined'){
            const off=new OffscreenCanvas(8,8),o=off.getContext('2d');o.fillStyle='red';o.fillRect(0,0,8,8);
            await probe('OffscreenCanvas',()=>x.drawImage(off,0,0),()=>{o.fillStyle='blue';o.fillRect(0,0,8,8)});
          }
          const image=new Image();image.src=source.toDataURL();await image.decode();
          c.width=64;x.drawImage(image,0,0);const imageBefore=pixel();
          z.fillStyle='green';z.fillRect(0,0,8,8);image.src=source.toDataURL();await image.decode();
          await tick('invert(1)');await tick('none');
          checks.HTMLImageElement={active:active(),before:imageBefore,after:pixel(),stable:JSON.stringify(imageBefore)===JSON.stringify(pixel())};
          c.width=64;const dash=[1,2];x.setLineDash(dash);dash[0]=8;
          await tick('invert(1)');await tick('none');
          checks.lineDash={active:active(),dash:x.getLineDash()};
          // Fallback must restore prior canonical pixels while dark, not retain
          // filtered pixels or a gradient that can mutate during later replay.
          c.width=64;await tick('invert(1)');x.fillStyle='white';x.fillRect(0,0,64,64);
          x.fillStyle=gradient;checks.canonicalFallback={fallback:!active(),white:pixel()};
          x.fillStyle='red';x.fillRect(0,0,8,8);x.strokeStyle=gradient;x.fillStyle=pattern;
          checks.repeatedFallback={fallback:!active(),red:pixel()};
          x.reset();checks.recovery={active:active()};
          return checks;
        }''')
    if args.case in ('blends', 'all'):
        result['cases']['blends'] = page.evaluate('''async () => {
          const checks={};
          for(const blend of ['multiply','screen','destination-out']){
            c.width=64;await tick('invert(1)');x.fillStyle='white';x.fillRect(0,0,64,64);
            x.globalCompositeOperation=blend;
            const fallback=!active();x.fillStyle='red';x.globalAlpha=.5;x.fillRect(0,0,8,8);
            const before=pixel();await tick('none');await tick('invert(1)');await tick('none');
            checks[blend]={fallback,before,after:pixel(),stable:JSON.stringify(before)===JSON.stringify(pixel())};
          }
          c.width=64;x.globalCompositeOperation='invalid';
          checks.invalid={active:active(),value:x.globalCompositeOperation};
          return checks;
        }''')
    result['page_errors'] = errors
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))
    browser.close()
assert not errors, errors
for name, checks in result['cases'].items():
    if name == 'resize':
        for check in checks:
            assert check['clear'] == [0,0,0,0], 'dimension reset resurrected stale pixels'
            assert check['active'], 'dimension reset did not recover image-safe mode'
            if 'newDraw' in check:
                assert check['newDraw'] == [0,0,255,255], 'resize discarded a subsequent synchronous draw'
    elif name == 'resources':
        for resource, check in checks.items():
            if resource == 'recovery':
                assert check['active'], 'reset did not recover from resource fallback'
            elif resource == 'canonicalFallback':
                assert check['fallback'] and check['white'] == [255,255,255,255], 'fallback did not restore canonical pixels'
            elif resource == 'HTMLImageElement':
                assert check['active'] and check['stable'], 'HTML image source was not snapshotted'
            elif resource == 'lineDash':
                assert check['active'] and check['dash'] == [1,2], 'mutable line dash was not copied'
            elif resource == 'repeatedFallback':
                assert check['fallback'] and check['red'] == [255,0,0,255], 'repeated fallback cleared canonical pixels'
            else:
                assert check['fallback'] and check['marked'] and check['stable'], resource+' must explicitly fall back before retaining a mutable resource'
    else:
        for blend, check in checks.items():
            if blend == 'invalid':
                assert check['active'] and check['value'] == 'source-over', 'invalid blend should preserve native state'
            else:
                assert check['fallback'] and check['stable'], blend+' must use canonical legacy composition'
print('PASS: replay safety regressions')
