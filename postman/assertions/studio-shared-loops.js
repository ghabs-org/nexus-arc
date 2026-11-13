const body = pm.response.json();
pm.test('Ordered shared loop conversion succeeds', () => {
  pm.response.to.have.status(200);
  pm.expect(body.valid).to.equal(true);
  pm.expect(body.diagnostics).to.deep.equal([]);
});
pm.test('Controllers retain isolated bodies and explicit ordering', () => {
  const graph = body.document.definitions[0].document.graph;
  pm.expect(graph.nodes.map(n => n.interop_body_id)).to.deep.equal(['body', 'body']);
  pm.expect(graph.edges).to.deep.include({source: 'first', target: 'second'});
  pm.expect(graph.nodes[0].body.nodes[0].id).to.equal('body');
  pm.expect(graph.nodes[1].body.nodes[0].id).to.equal('body');
});
pm.test('Original source references survive conversion', () => {
  const doc = body.document.definitions[0].document;
  pm.expect(doc.metadata.interop_workflow_source.nodes[2].input.previous).to.equal('{{body.output.total}}');
});
