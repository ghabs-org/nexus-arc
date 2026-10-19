const body=pm.response.json(),proposal=body.proposal;
pm.test('Reviewed bundle retains new named children',()=>{
 pm.response.to.have.status(200);
 pm.expect(body.status).to.equal('saved');
 pm.expect(proposal.workflows).to.have.lengthOf(2);
 const ids=proposal.workflows.map(w=>w.id);
 pm.expect(ids).to.include(proposal.workflow.graph.nodes[0].workflow_id);
 pm.expect(ids).to.include(proposal.workflows[0].graph.nodes[0].workflow_id);
});
pm.test('Phone edits preserve advanced agent values and resource references',()=>{
 const agent=proposal.agents[0];
 pm.expect(agent.timeout_seconds).to.equal(35);
 pm.expect(agent.max_model_calls).to.equal(3);
 pm.expect(agent.model_id).to.be.a('string');
 pm.expect(agent.skill_ids.length).to.be.above(0);
 pm.expect(agent.output_schema.type).to.equal('integer');
});
pm.test('Child edit belongs to reviewed saved release',()=>{
 pm.expect(proposal.workflows[1].name).to.equal('Phone edited local word count');
 pm.expect(body.release_id).to.be.a('string').and.not.empty;
 pm.expect(body.revision).to.equal(4);
});
