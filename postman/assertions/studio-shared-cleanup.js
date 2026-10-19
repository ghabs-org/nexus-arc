const body=pm.response.json();
pm.test('Shared cleanup has two completed phase receipts',()=>{
 pm.response.to.have.status(200);
 const completed=Object.entries(body.states).filter(([key,status])=>key.endsWith('/words')&&status==='completed').map(([key])=>key);
 pm.expect(completed).to.have.lengthOf(2);
 pm.expect(completed.some(key=>key.includes('/cleanup/always/'))).to.equal(true);
 pm.expect(completed.some(key=>key.includes('/cleanup/on_success/'))).to.equal(true);
});
pm.test('Replay graph retains each phase identity',()=>{
 const ids=body.graph.nodes.map(node=>node.id);
 pm.expect(ids.some(id=>id.includes('/cleanup/always/'))).to.equal(true);
 pm.expect(ids.some(id=>id.includes('/cleanup/on_success/'))).to.equal(true);
});
pm.test('Execution replay remains read only',()=>{
 pm.expect(body.replay.read_only).to.equal(true);
 pm.expect(body.timeline.length).to.be.above(0);
});
