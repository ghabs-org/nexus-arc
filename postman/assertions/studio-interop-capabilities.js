const body=pm.response.json();
pm.test('Declarative capability conversion succeeds',()=>{
 pm.response.to.have.status(200);
 pm.expect(body.valid).to.equal(true);
 pm.expect(body.diagnostics).to.deep.equal([]);
});
pm.test('Routing capabilities and knowledge resources remain distinct',()=>{
 const spec=body.document.definitions[0].document.spec;
 pm.expect(spec.agent_card.skills[0].id).to.match(/^cap-[a-f0-9]{16}$/);
 pm.expect(spec.agent_card.skills[0].name).to.equal('Risk review');
 pm.expect(spec.skill_ids).to.deep.equal(['knowledge']);
 pm.expect(spec.toolset_ids).to.deep.equal(['local-tools']);
});
pm.test('Source stays lossless without fabricated IDs or secrets',()=>{
 const row=body.document.definitions[0];
 pm.expect(row.document.metadata.interop_agent_source.source.skills[0]).not.to.have.property('id');
 pm.expect(body.document.resources).to.deep.equal([]);
 pm.expect(row.document.spec.provider).to.equal('codex');
});
