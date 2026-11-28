pm.test('Saved evaluator exposes validated provider and fixed choice scores',()=>{
 pm.response.to.have.status(200);
 const item=pm.response.json().items.find(item=>item.id==='evidence-judge');
 pm.expect(item.revision).to.eql(1);
 pm.expect(item.data.kind).to.eql('evaluator');
 pm.expect(item.data.type).to.eql('llm_judge');
 pm.expect(item.data.provider).to.eql('codex');
 pm.expect(item.data.threshold).to.eql(0.5);
 pm.expect(item.data.choices).to.eql({Supported:1,Partial:0.5,Unsupported:0});
});
