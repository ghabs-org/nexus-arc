pm.test('Queued voice exposes revision metadata without private settings',function(){
 pm.response.to.have.status(202);
 const run=pm.response.json();
 pm.expect(run.kind).to.eql('speech');
 pm.expect(run.status).to.eql('queued');
 pm.expect(run.speech_profile).to.eql('openai-speech');
 pm.expect(run.speech_action).to.eql('speak');
 pm.expect(run.speech_profile_revision).to.match(/^[a-f0-9]{64}$/);
 pm.expect(run).not.to.have.property('_speech_profile_snapshot');
 pm.expect(JSON.stringify(run)).not.to.include('CLIENT_SECRET');
 pm.expect(JSON.stringify(run)).not.to.include('OPENAI_API_KEY');
});
