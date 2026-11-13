pm.test("Native finance run preserves typed quote or fundamental results", function () {
  pm.response.to.have.status(200);
  const run=pm.response.json();
  pm.expect(run.status).to.equal("completed");
  const result=JSON.parse(run.output);
  pm.expect(result.status).to.equal("success");
  pm.expect(result.ticker).to.equal("EXAMPLE");
  pm.expect(result.source_url).to.equal("https://finance.yahoo.com/quote/EXAMPLE/");
  if(result.operation==="price") {
    pm.expect(result.current_price).to.equal(125.5);
    pm.expect(result.change_percent).to.equal(4.58);
  } else {
    pm.expect(result.operation).to.equal("fundamentals");
    pm.expect(result.company_name).to.equal("Example Company");
    pm.expect(result.dividend_yield).to.equal(0);
    pm.expect(result.revenue).to.equal(null);
  }
});
