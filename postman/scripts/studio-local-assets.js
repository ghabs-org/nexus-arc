const font = pm.request.url.toString().endsWith(".woff2");
pm.test("Public asset is available", () => pm.expect(pm.response.code).to.equal(200));
pm.test("Asset has the expected media type", () => pm.expect(pm.response.headers.get("Content-Type")).to.include(font ? "font/woff2" : "image/svg+xml"));
pm.test("Content sniffing stays disabled", () => pm.expect(pm.response.headers.get("X-Content-Type-Options")).to.equal("nosniff"));
pm.test("Studio content isolation stays enabled", () => {
  const policy = pm.response.headers.get("Content-Security-Policy");
  pm.expect(policy).to.include("default-src 'self'");
  pm.expect(policy).to.include("frame-ancestors 'none'");
});
pm.test("Generic response caching stays disabled", () => pm.expect(pm.response.headers.get("Cache-Control")).to.equal("no-store"));
pm.test("Response contains the requested static asset", () => {
  if (font) pm.expect(pm.response.text().slice(0, 4)).to.equal("wOF2");
  else pm.expect(pm.response.text()).to.include('<symbol id="search"');
});
