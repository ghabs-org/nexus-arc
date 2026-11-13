const body = pm.response.json();
pm.test('Concurrent shared loop controllers are diagnosed', () => {
  pm.response.to.have.status(200);
  pm.expect(body.valid).to.equal(false);
  pm.expect(body.document).to.equal(null);
  pm.expect(body.diagnostics.some(d => d.message.includes('dependency-ordered'))).to.equal(true);
});
