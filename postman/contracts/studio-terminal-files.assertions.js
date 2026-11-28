pm.test("Generated artifact original bytes remain downloadable", function () {
  pm.response.to.have.status(200);
  pm.expect(pm.response.headers.get("Content-Type")).to.equal("application/octet-stream");
  pm.expect(pm.response.headers.get("Cache-Control")).to.equal("no-store");
  pm.expect(pm.response.text()).to.equal("Reviewed evidence");
});
