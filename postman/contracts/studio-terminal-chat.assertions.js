pm.test("Pinned attachment result remains inspectable", function () {
  pm.response.to.have.status(200);
  const run = pm.response.json();
  pm.expect(run.status).to.equal("completed");
  pm.expect(run.output).to.equal("Reviewed example evidence");
  pm.expect(run.artifact_refs).to.deep.equal([{id: "example-evidence", revision: 1}]);
  pm.expect(Object.keys(run.artifact_refs[0]).sort()).to.deep.equal(["id", "revision"]);
});
