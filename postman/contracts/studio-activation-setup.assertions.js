pm.test('Lifecycle exposes missing local setup without claiming live health',function(){
 pm.response.to.have.status(200);
 const data=pm.response.json();
 const blocked=data.components.find(component=>component.issues.includes('worker_profile_missing:python'));
 pm.expect(blocked).to.be.an('object');
 pm.expect(blocked.ready).to.eql(false);
 pm.expect(blocked.prerequisite_checks).to.deep.include({name:'python worker',status:'missing'});
 pm.expect(blocked.prerequisite_fingerprint).to.match(/^[a-f0-9]{64}$/);
 pm.expect(data.live_health).to.eql('not_checked');
});
