pm.test("Local terminal deletion preserves named server history", function () {
  pm.response.to.have.status(200);
  const session = pm.response.json();
  pm.expect(session.title).to.equal("Named acceptance");
  pm.expect(session.archived).not.to.equal(true);
  pm.expect(session.messages.map(message => message.role)).to.deep.equal(["user", "assistant"]);
  pm.expect(session.messages[0].content).to.equal("Acceptance evidence");
  pm.expect(session.messages[1].content).to.equal("Reviewed evidence");
});
