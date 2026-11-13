pm.test('Feedback history is current, unique per run and private to its account', () => {
  pm.response.to.have.status(200);
  const cookie = pm.request.headers.get('Cookie') || '';
  const owner = cookie.includes('a'.repeat(32)) ? 'alice' : cookie.includes('b'.repeat(32)) ? 'bob' : null;
  pm.expect(owner, 'Use the disposable fixture Alice/Bob session').not.to.eql(null);
  const items = pm.response.json().items;
  pm.expect(items.every(item => item.owner_id === owner)).to.eql(true);
  pm.expect(new Set(items.map(item => item.run_id)).size).to.eql(items.length);
  if (owner === 'alice') {
    pm.expect(items).to.have.length(2);
    const edited = items.find(item => item.run_id === 'feedback-new');
    pm.expect(edited.revision).to.eql(2);
    pm.expect(edited.rating).to.eql(1);
    pm.expect(edited.comment).to.eql('Clear and useful.\nKeep sources visible.');
    pm.expect(JSON.stringify(items)).not.to.include('BOB_PRIVATE_COMMENT');
  } else {
    pm.expect(items).to.have.length(1);
    pm.expect(items[0].comment).to.eql('BOB_PRIVATE_COMMENT');
  }
});
