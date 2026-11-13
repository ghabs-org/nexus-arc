pm.test('Snapshot contains only selected pinned files and stays private', () => {
  pm.response.to.have.status(201);
  const snapshot = pm.response.json();
  pm.expect(snapshot.public).to.eql(false);
  pm.expect(snapshot).not.to.have.property('token');
  pm.expect(snapshot).not.to.have.property('token_hash');
  pm.expect(snapshot.artifacts.map(file => file.id)).to.eql(['file-0', 'file-1']);
  snapshot.artifacts.forEach(file => {
    pm.expect(file.revision).to.eql(1);
    pm.expect(file.filename).to.be.a('string');
  });
});
