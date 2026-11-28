pm.test("Directed terminal message records selected agent and literal body", function () {
  pm.response.to.have.status(200);
  const session = pm.response.json();
  pm.expect(session.agent_id).to.equal("reviewer");
  pm.expect(session.provider).to.equal("codex");
  pm.expect(session.messages.map(message => message.role)).to.deep.equal(["user", "assistant"]);
  pm.expect(session.messages[0].content).to.equal('Acceptance {"source": "local"}');
  pm.expect(session.messages[1].content).to.equal("Reviewed evidence");
});
