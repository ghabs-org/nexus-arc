pm.test('Nuclia descriptor conversion preserves reviewed connection binding and fixed settings', function () {
  pm.response.to.have.status(200);
  const report = pm.response.json();
  pm.expect(report.valid).to.eql(true);
  pm.expect(report.diagnostics).to.eql([]);
  pm.expect(report.document.resources).to.eql([]);
  const agent = report.document.definitions[0].document;
  pm.expect(agent.spec.connector_ids).to.eql(['knowledge']);
  pm.expect(agent.metadata.interop_agent_source.bindings.nuclia.CompanyKnowledge)
    .to.eql({connector_id:'knowledge',policy:'nexus-strict-v1'});
  pm.expect(report.adaptations[0].policy).to.eql('nexus-strict-v1');
  pm.expect(report.adaptations[0].changes.join(' ')).to.include('ephemeral');
  pm.expect(JSON.stringify(report)).not.to.include('synthetic-nuclia-key');
});
