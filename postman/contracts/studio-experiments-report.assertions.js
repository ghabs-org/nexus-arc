pm.test('Completed comparison rejects weak variant despite adequate aggregate pass rate',()=>{
 pm.response.to.have.status(200);
 const reports=pm.response.json().items;
 pm.expect(reports).to.have.lengthOf(1);
 const report=reports[0];
 pm.expect(report.experiment_revision).to.eql(2);
 pm.expect(report.suite_revision).to.eql(1);
 pm.expect(report.complete).to.eql(true);
 pm.expect(report.acceptance).to.eql(false);
 pm.expect(report.total).to.eql(4);
 pm.expect(report.expected_total).to.eql(4);
 pm.expect(report.passed).to.eql(2);
 pm.expect(report.variant_results.map(v=>v.accepted)).to.eql([true,false]);
 pm.expect(report.results.map(r=>r.repetition)).to.eql([1,2,1,2]);
});
