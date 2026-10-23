# Image-safe canvas candidate

## Reproduce the checks

Prerequisites: Node/npm, Python with `playwright` and `Pillow`, and Playwright Chromium installed.

```sh
npm ci
npm run check
npx tsc --noEmit
npm run build
python tests/image_safe_canvas.py
python tests/live_google_docs.py
```

The fixture test originally failed on image colours with the existing CSS inversion. The live Google Docs test also failed against the original packaged extension. Both now exercise real Chromium rendering, not mocked canvas calls.

The live test opens AgricultureAlex's public test document without signing in. It installs the built extension into an isolated temporary Chromium profile and changes extension settings only. It does not type into or edit the document. It checks an opaque image region against an unmodified browser rendering, readable text, dark background, popup Off/Dark, live Auto light/dark switching and the highlight toggle. Screenshots and numerical results are written under `test-results/`.

## Implementation

A document-start MAIN-world script filters text/vector canvas operations using the existing filter string, leaving `drawImage` unchanged. Only `.kix-canvas-tile-content` contexts are affected. The matching canvas CSS filter is disabled once the hook has initialised. A command log enables repainting when settings change without reloading or editing the document. Native drawing state and errors are preserved. Mutable canvas image sources are snapshotted for replay. Canvas resizing clears the log; weak references avoid retaining discarded canvases.

## Deliberate limits — not a universal release-ready renderer

- Verified in Chromium on Linux, including the real public Google Docs test document. Firefox, Safari and macOS Chrome are unverified.
- Canvas implementations without `filter` or `reset` retain the original CSS rendering.
- After 30,000 recorded commands per canvas, the hook deliberately restores the original pixels and falls back to the existing CSS inversion until the canvas is resized. Images can therefore invert again in a sufficiently long editing session. The regression checks exercise this fallback and recovery.
- Long-document performance and prolonged editing sessions remain unverified.
- Replay is not a complete implementation of every mutable Canvas API resource: changing an existing gradient, pattern, Path2D, ImageData or non-canvas image source after drawing is not covered. CanvasPattern-based photographs and OffscreenCanvas/WebGL rendering are outside the verified scenario.
- Filters applied per draw can differ from a final composited filter for overlapping/translucent vectors, shadows and unusual blend modes. The opaque photo, transparent photo, text and highlight examples in the supplied live document render correctly, but arbitrary drawings still need broader testing.
- No user-agent spoofing, network interception or account credentials are used.
- Nothing has been committed, pushed or posted to GitHub.
