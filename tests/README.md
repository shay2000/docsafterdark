# Experimental image-safe canvas candidate — 2.0.6-alpha.2

**Not release ready. The live Chromium photo-preservation test fails.** HTML image
snapshots exhaust the 16 MiB per-canvas budget on the supplied public document;
legacy CSS fallback preserves canonical drawing/controls but inverts photographs.
The fallback is actual observed behaviour, not just a theoretical long-session
limit. Do not interpret a legacy-reference pass as image preservation.

## Reproduce the checks

Prerequisites: Node/npm, Python with `playwright` and `Pillow`, and Playwright
Chromium and Firefox installed. Firefox live testing additionally needs Firefox
and an official platform-matching geckodriver executable.

```sh
npm ci
npm run check
npx tsc --noEmit
npm run build
python tests/image_safe_canvas.py
python tests/renderer_regressions.py --out test-results/regressions-chromium.json
python tests/renderer_regressions.py --browser firefox --out test-results/regressions-firefox.json
python tests/renderer_stress.py --assert-regressions --out test-results/stress-chromium.json
python tests/renderer_stress.py --browser firefox --assert-regressions --out test-results/stress-firefox.json
```

The fixture checks supported image draws, readable text, dark background and
Off/On replay with real browser pixels. Regression/stress suites check native
reset pruning, snapshot budget/refunds, attribute and property dimension resets,
mutable-resource fallback, copied line dash, image snapshots, blend fallback and
repeated fallback idempotence. The stress timer includes a clamped zero-delay
timer; it is not a full GPU-paint or production-session benchmark.

### Live Chromium: keep two separate verdicts

```sh
# Strict image preservation: currently FAILS with snapshot-budget-or-size fallback.
python tests/live_google_docs.py --cycles 5 --out test-results/chromium-strict
# Explicit legacy correctness only: does NOT establish photo preservation.
python tests/live_google_docs.py --allow-legacy-fallback --cycles 5 --out test-results/chromium-legacy
```

The read-only test opens AgricultureAlex's public document without signing in,
installs the built extension in a disposable profile and changes extension
settings only. It neither types into nor edits the document. Each of the 40
additional transitions checks the renderer marker, brightness and an opaque photo
crop. The strict default rejects fallback. With the explicit legacy flag, the
reference is the no-extension canvas with the exact computed legacy CSS filter;
results retain the error against the unfiltered original separately. Actual
media-query change events are counted. `--build /absolute/unpacked/build` selects
an archived build; `--out` selects screenshots/JSON evidence.

In the final Chromium 156 run, strict mode failed with
`data-dad-canvas-fallback="snapshot-budget-or-size"`. The explicit legacy run
passed 40 transitions and 10 media events with no page JavaScript errors and zero
initial reference error, but initial original-photo channel mean error was
approximately [164.57, 165.09, 166.25] on a 0–255 scale. The screenshot visibly
shows inverted/washed-out cars. This is a real loss of photo preservation versus
the earlier alpha.1 image-safe scenario, accepted only as an experimental
correctness/resource-budget trade-off, not a resolved product regression.

### Live Firefox

```sh
python tests/live_firefox_docs.py --geckodriver /absolute/path/to/geckodriver
```

The package defaults to the version in `package.json`; `--package` can select an
explicit ZIP. Disposable WebDriver profiles install it temporarily. The recorded
Firefox 140.17 ESR Linux ARM64 run preserved the measured photo crop (zero channel
mean error) across 18 Off/Dark, highlight and quick-toggle transitions. It did not
test live Auto or capture startup console errors. Playwright Firefox 157 passed
renderer fixtures, but its WebDriver policy blocked popup navigation in prior
live testing; its live control coverage remains incomplete. Do not bypass that
security policy or infer cross-browser success from ESR.

## Implementation and limits

- A document-start MAIN-world hook affects only `.kix-canvas-tile-content`
  contexts, filters vector/text operations and leaves supported image draws
  unfiltered. A replay log allows settings changes without document edits.
- Native `reset()` prunes obsolete history. Width/height property and attribute
  writes clear history, including synchronous post-resize draws and detached tiles
  covered by the regression fixture. Weak references avoid a global strong tile list.
- Mutable HTML canvas and HTML image sources are copied per draw. This protects
  replay from later source mutations but adds payload and can cause earlier fallback.
- After 30,000 commands, 16 MiB of estimated RGBA snapshots, or unsupported resources,
  restore canonical pixels and use the original CSS inversion until reset or resize.
  `data-dad-canvas-fallback` records the reason. Fallback allocates no discarded
  snapshots. The budget is not a total browser/GPU memory bound.
- ImageData, Path2D, gradients, patterns, OffscreenCanvas/video/bitmap sources and
  non-source-over composition conservatively use legacy rendering. The regressions
  assert stable canonical pixels, not image-safe support for those APIs.
- Per-draw filtering can differ from post-composition filtering for translucent
  source-over vectors, shadows and overlapping content. Blend fallback does not
  prove universal Canvas equivalence. WebGL remains outside scope.
- Canvas implementations without `filter` or `reset` keep legacy CSS rendering.
  Safari/macOS Chrome, prolonged editing, long-document scrolling/GC and arbitrary
  drawings remain unverified. Linux photo-crop evidence is not a universal guarantee.
- No user-agent spoofing, network interception, account credentials, permission
  expansion or document edits are used. This is an experimental fork branch only,
  not a store/upstream release.
