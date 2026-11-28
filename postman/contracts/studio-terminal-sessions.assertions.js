pm.test("Conversation export preserves authorized session history", function () {
  pm.response.to.have.status(200);
  const exported = pm.response.json();
  pm.expect(exported.format).to.equal("nexus-studio-memory/v1");
  pm.expect(exported.conversation.title).to.equal("Terminal acceptance");
  pm.expect(exported.conversation.messages.map(message => message.role)).to.deep.equal(["user", "assistant"]);
  pm.expect(exported.conversation.messages[0].content).to.equal("Acceptance evidence");
  pm.expect(exported.conversation.messages[1].content).to.equal("Reviewed evidence");
});
