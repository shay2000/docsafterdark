"""Bounded, real-browser replay/memory probes; no network or document edits.

Use --assert-regressions for the new resource/reset acceptance checks.
Mutable resources and non-source-over blending must take the safe legacy path.
"""
import argparse
import json
import pathlib
import statistics
import subprocess
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--source', type=pathlib.Path, default=ROOT / 'src/image-safe-canvas.ts')
parser.add_argument('--browser', choices=['chromium', 'firefox'], default='chromium')
parser.add_argument('--out', type=pathlib.Path, default=ROOT / 'test-results/stress.json')
parser.add_argument('--assert-regressions', action='store_true')
args = parser.parse_args()
args.out.parent.mkdir(parents=True, exist_ok=True)
script = subprocess.check_output([
    'node', '--input-type=module', '-e',
    "import ts from 'typescript'; import fs from 'node:fs'; console.log(ts.transpile(fs.readFileSync(process.argv[1],'utf8'),{target:ts.ScriptTarget.ES2022}));",
    str(args.source),
], cwd=ROOT, text=True)

with sync_playwright() as p:
    browser = getattr(p, args.browser).launch(headless=True)
    page = browser.new_page(viewport={'width': 700, 'height': 320})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.set_content('<canvas class="kix-canvas-tile-content" width="600" height="240"></canvas>')
    support = page.evaluate("({filter:'filter' in CanvasRenderingContext2D.prototype,reset:'reset' in CanvasRenderingContext2D.prototype})")
    # Count actual native draws and allocated snapshot pixels without exposing
    # renderer internals or relying on GC/JS-heap estimates of native bitmaps.
    page.evaluate('''() => {
      window.nativeFills=0;window.copies=0;window.copyBytes=0;
      const fill=CanvasRenderingContext2D.prototype.fillRect;
      CanvasRenderingContext2D.prototype.fillRect=function(...a){nativeFills++;return fill.apply(this,a)};
      const create=document.createElement.bind(document);
      document.createElement=function(...a){const c=create(...a);
        if(a[0]==='canvas'){copies++; const get=c.getContext.bind(c);
          c.getContext=function(...b){copyBytes+=c.width*c.height*4;return get(...b)};}
        return c;};
    }''')
    page.evaluate(script)
    result = {'browser': args.browser, 'version': browser.version, 'support': support, 'source': str(args.source)}
    if not all(support.values()):
        page.evaluate("document.querySelector('canvas').getContext('2d').fillRect(0,0,1,1)")
        result['unsupported_fallback_unmarked'] = page.locator('canvas').get_attribute('data-dad-image-safe') is None
    else:
        page.evaluate('''() => {
          window.c=document.querySelector('canvas');window.x=c.getContext('2d');
          window.tick=async value=>{document.documentElement.setAttribute('data-dad-canvas-filter',value);
            await new Promise(resolve=>setTimeout(resolve,0));};
          window.pixel=()=>Array.from(x.getImageData(2,2,1,1).data);
          window.active=()=>c.hasAttribute('data-dad-image-safe');
        }''')
        result['reset_cycles'] = page.evaluate('''async () => {
          c.width=600;for(let frame=0;frame<4000;frame++){
            x.reset();x.fillStyle='white';x.fillRect(0,0,600,240);}
          const fills=nativeFills, times=[];
          for(let i=0;i<20;i++){const start=performance.now();
            await tick(i%2?'none':'invert(1)');times.push(performance.now()-start);}
          return {active:active(),native_replay_fills:nativeFills-fills,toggle_ms:times,pixel:pixel()};
        }''')
        result['reset_cycles']['median_toggle_ms'] = statistics.median(result['reset_cycles']['toggle_ms'])
        result['reset_recovery'] = page.evaluate('''async () => {
          c.width=600;for(let i=0;i<30010;i++)x.fillRect(0,0,1,1);
          const fallback=!active();x.reset();x.fillStyle='white';x.fillRect(0,0,600,240);
          await tick('invert(1)');const dark=pixel();await tick('none');
          return {fallback,recovered:active(),dark,light:pixel()};
        }''')
        result['snapshot_budget'] = page.evaluate('''async () => {
          c.width=600;const s=document.createElement('canvas');s.width=s.height=512;
          const z=s.getContext('2d');z.fillStyle='#e04020';z.fillRect(0,0,512,512);
          const before=copies,bytes=copyBytes;
          for(let i=0;i<24;i++)x.drawImage(s,0,0);
          const r={copies:copies-before,bytes:copyBytes-bytes,active:active(),pixel:pixel()};
          s.width=1;await tick('invert(1)');await tick('none');r.after= pixel();return r;
        }''')
        result['budget_recovery'] = page.evaluate('''() => {
          const s=document.createElement('canvas');s.width=s.height=512;
          x.reset();for(let i=0;i<16;i++)x.drawImage(s,0,0);
          const resetRecovered=active();
          x.drawImage(s,0,0);const exceeded=!active();
          c.width=600;for(let i=0;i<16;i++)x.drawImage(s,0,0);
          const resizeRecovered=active();
          c.width=600;for(let i=0;i<20;i++){try{x.drawImage(s)}catch(_){}}
          for(let i=0;i<16;i++)x.drawImage(s,0,0);
          return {resetRecovered,exceeded,resizeRecovered,invalidCallsRefunded:active()};
        }''')
        result['reset_state'] = page.evaluate('''async () => {
          c.width=600;x.save();x.translate(100,100);x.globalAlpha=.25;
          x.beginPath();x.rect(100,100,1,1);x.clip();x.reset();x.restore();
          x.fillStyle='red';x.fillRect(0,0,8,8);const before=pixel();
          await tick('invert(1)');await tick('none');
          return {before,after:pixel(),alpha:x.globalAlpha,identity:x.getTransform().isIdentity};
        }''')
        result['fallback_copies'] = page.evaluate('''() => {
          c.width=600;for(let i=0;i<30010;i++)x.fillRect(0,0,1,1);
          const s=document.createElement('canvas');s.width=s.height=64;
          const before=copies;for(let i=0;i<12;i++)x.drawImage(s,0,0);
          return {copies:copies-before,active:active()};
        }''')
        result['resize_and_state'] = page.evaluate('''async () => {
          c.width=600;x.fillStyle='red';x.fillRect(0,0,20,20);
          c.height=240;x.save();x.translate(1,1);x.globalAlpha=.5;
          x.fillStyle='#e04020';x.fillRect(0,0,10,10);x.restore();
          const before=pixel();await tick('invert(1)');await tick('none');
          return {active:active(),before,after:pixel(),alpha:x.globalAlpha,transform:x.getTransform().isIdentity};
        }''')
        result['attribute_resize_diagnostic'] = page.evaluate('''async () => {
          c.width=600;x.fillStyle='red';x.fillRect(0,0,8,8);
          c.setAttribute('width','600');const before=pixel();
          await tick('invert(1)');await tick('none');
          return {before,after:pixel(),stable:JSON.stringify(before)===JSON.stringify(pixel())};
        }''')
        result['mutable_diagnostics'] = page.evaluate('''async () => {
          const checks={};
          const probe=async(name,draw,mutate)=>{
            c.width=600;draw();const before=pixel();mutate();
            await tick('invert(1)');await tick('none');
            checks[name]={before,after:pixel(),fallback:!active(),stable:JSON.stringify(before)===JSON.stringify(pixel())};};
          const data=new ImageData(8,8);data.data.fill(255);
          await probe('ImageData',()=>x.putImageData(data,0,0),()=>data.data.fill(0));
          const path=new Path2D();path.rect(0,0,8,8);
          await probe('Path2D',()=>{x.fillStyle='red';x.fill(path)},()=>path.rect(0,0,20,20));
          // Add a new region to the path and test that region, not its old interior.
          c.width=600;const q=new Path2D();q.rect(20,20,5,5);x.fill(q);
          const prior=pixel();q.rect(0,0,8,8);await tick('invert(1)');await tick('none');
          checks.Path2D={before:prior,after:pixel(),fallback:!active(),stable:JSON.stringify(prior)===JSON.stringify(pixel())};
          const gradient=x.createLinearGradient(0,0,8,0);gradient.addColorStop(0,'red');gradient.addColorStop(1,'red');
          await probe('gradient',()=>{x.fillStyle=gradient;x.fillRect(0,0,8,8)},()=>gradient.addColorStop(.5,'blue'));
          if(typeof OffscreenCanvas!=='undefined'){
            const off=new OffscreenCanvas(8,8),z=off.getContext('2d');z.fillStyle='red';z.fillRect(0,0,8,8);
            await probe('OffscreenCanvas',()=>x.drawImage(off,0,0),()=>{z.fillStyle='blue';z.fillRect(0,0,8,8)});}
          return checks;
        }''')
        result['transparency_blend'] = page.evaluate('''async () => {
          const checks={};
          for(const blend of ['source-over','multiply','screen','destination-out']){
            c.width=600;x.fillStyle='white';x.fillRect(0,0,600,240);
            x.globalAlpha=.5;x.fillStyle='red';x.fillRect(0,0,20,20);
            x.globalCompositeOperation=blend;x.fillStyle='blue';x.fillRect(0,0,10,10);
            const before=pixel();await tick('invert(1)');const dark=pixel();await tick('none');
            checks[blend]={before,dark,expected_post_composite:before.slice(0,3).map(v=>255-v).concat(before[3]),
              after:pixel(),fallback:!active(),roundtrip:JSON.stringify(before)===JSON.stringify(pixel())};
          }return checks;
        }''')
        page.screenshot(path=str(args.out.with_suffix('.png')))
    result['page_errors'] = errors
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))
    browser.close()

assert not errors, errors
if args.assert_regressions and all(support.values()):
    assert result['reset_cycles']['native_replay_fills'] == 20, 'reset retained obsolete draw commands'
    assert result['reset_recovery']['recovered'], 'reset did not recover from fallback'
    assert result['reset_recovery']['dark'] == [0,0,0,255]
    assert result['reset_recovery']['light'] == [255,255,255,255]
    assert result['snapshot_budget']['bytes'] <= 16*1024*1024, 'snapshot payload exceeded 16 MiB budget'
    assert not result['snapshot_budget']['active'], 'snapshot budget did not fall back'
    assert result['snapshot_budget']['pixel'] == [224,64,32,255], 'budget fallback lost current draw'
    assert result['snapshot_budget']['after'] == [224,64,32,255], 'budget fallback mutated pixels'
    assert all(result['budget_recovery'].values()), 'snapshot budget did not reset/refund correctly'
    assert result['reset_state']['before'] == [255,0,0,255], 'reset retained clip/stack/transform'
    assert result['reset_state']['after'] == result['reset_state']['before']
    assert result['reset_state']['alpha'] == 1 and result['reset_state']['identity']
    assert result['fallback_copies']['copies'] == 0, 'fallback still allocates discarded snapshots'
    assert result['resize_and_state']['before'] == result['resize_and_state']['after']
    assert result['resize_and_state']['active']
    assert result['resize_and_state']['alpha'] == 1
    assert result['resize_and_state']['transform']
    assert result['attribute_resize_diagnostic']['stable'], 'attribute resize resurrected stale pixels'
    assert all(v['stable'] and v['fallback'] for v in result['mutable_diagnostics'].values())
    assert all(v['fallback'] for k,v in result['transparency_blend'].items() if k != 'source-over')
    assert all(v['roundtrip'] for v in result['transparency_blend'].values())
print('PASS: supported acceptance checks' if all(support.values()) else 'UNSUPPORTED: native filter/reset missing; legacy CSS fallback only')
