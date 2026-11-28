pm.test("Studio help and file previews retain their read-only contract", function () {
  pm.response.to.have.status(200);
  const result = pm.response.json();
  if (result.mode === "local-help") {
    pm.expect(result.sources.some(source => source.id === "storage")).to.equal(true);
    pm.expect(result.workspace.agents).to.match(/\/agents$/);
    pm.expect(result.notice).to.include("No model call");
  } else {
    pm.expect(result.root).to.equal("files");
    pm.expect(result.path).to.equal("VM notes.txt");
    pm.expect(result.text).to.include("Example file on the server");
    pm.expect(result.revision).to.match(/^[a-f0-9]{64}$/);
  }
});
