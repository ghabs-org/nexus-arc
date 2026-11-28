pm.test('Explicit evaluation records fixed choice score and copied suite revision',()=>{
 pm.response.to.have.status(200);
 const items=pm.response.json().items;
 pm.expect(items).to.have.lengthOf(1);
 const report=items[0],check=report.results[0].checks.find(check=>check.type==='choice_judge');
 pm.expect(report.suite_revision).to.eql(2);
 pm.expect(report.passed).to.eql(1);
 pm.expect(report.total).to.eql(1);
 pm.expect(check.choice).to.eql('Partial');
 pm.expect(check.score).to.eql(0.5);
 pm.expect(check.threshold).to.eql(0.5);
 pm.expect(check.passed).to.eql(true);
 pm.expect(check.provider).to.eql('codex');
});
