pm.test("Explicit retry adds exactly one matching chat turn", function () {
  pm.response.to.have.status(200);
  const session = pm.response.json();
  pm.expect(session.agent_id).to.equal("reviewer");
  pm.expect(session.provider).to.equal("codex");
  pm.expect(session.messages.map(message => message.role)).to.deep.equal(["user", "assistant", "user", "assistant"]);
  pm.expect(session.messages.filter(message => message.role === "user").map(message => message.content)).to.deep.equal([
    'Acceptance {"source": "local"}', 'Acceptance {"source": "local"}'
  ]);
  const replies = session.messages.filter(message => message.role === "assistant");
  pm.expect(replies.map(message => message.content)).to.deep.equal(["Reviewed evidence", "Reviewed evidence"]);
  pm.expect(replies[0].run_id).not.to.equal(replies[1].run_id);
});
