const body=pm.response.json();
pm.test('HTTP+JSON returns a scoped task collection without RPC envelope',()=>{
 pm.response.to.have.status(200);
 pm.expect(pm.response.headers.get('Content-Type')).to.include('application/a2a+json');
 pm.expect(body).not.to.have.property('jsonrpc');
 pm.expect(body.totalSize).to.equal(1);
});
pm.test('Cross-binding example created exactly one completed task',()=>{
 pm.expect(body.tasks).to.have.lengthOf(1);
 pm.expect(body.tasks[0].status.state).to.equal('TASK_STATE_COMPLETED');
 pm.expect(body.tasks[0].artifacts[0].parts[0].text).to.equal('3');
});
