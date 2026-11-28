# Studio local font/icon assets

Public static assets use the existing exact-path asset inventory. Fonts return
`font/woff2`; the local SVG symbol sprite returns `image/svg+xml`. Binary bytes
and glyph source hashes are recorded in `vendor/studio-assets.json`; original
licenses ship with the files. No font CDN or icon runtime is needed.

Each response retains `X-Content-Type-Options: nosniff`, the existing Studio CSP
and `Cache-Control: no-store`. Explicit service-worker caching is restricted to
the public shell allowlist. This does not enable generic filesystem access or
cache account data. Package data includes WOFF2/SVG files alongside license and
provenance records. The native build is not recreated by a Python wheel check.

Use `postman/scripts/studio-local-assets.js` as a local post-request script for
one WOFF2 and the sprite URL. It checks status, MIME, signature/content marker,
no-sniff, CSP and cache headers. Disable event reporting with `--no-report-events`
when exercising a disposable local preview. No credentials or workspace binding
are required for these public static GETs; no remote workspace publication occurs.
