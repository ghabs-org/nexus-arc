pm.test('Read-only snapshot exposes only its selected files and shared messages', () => {
  pm.response.to.have.status(200);
  const snapshot = pm.response.json();
  ['token_hash', 'owner_id', 'recipients', 'session_id'].forEach(field => {
    pm.expect(snapshot).not.to.have.property(field);
  });
  pm.expect(snapshot.artifacts.map(file => file.id)).to.eql(['selected']);
  pm.expect(snapshot.artifacts[0].revision).to.eql(1);
  pm.expect(snapshot.messages).to.have.length(2);
  pm.expect(snapshot.messages[1].content).to.include('Policy review');
});
