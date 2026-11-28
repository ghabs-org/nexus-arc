pm.test("Repository endpoint succeeds", () => pm.response.to.have.status(200));
const repository = pm.response.json();
pm.test("Repository identity matches selected target", () => {
  pm.expect(repository.full_name).to.equal("ghabs-org/nexus-arc");
});
pm.test("Repository numeric ID is valid", () => {
  pm.expect(repository.id).to.be.a("number").and.above(0);
});
pm.test("Selected repository is public", () => {
  pm.expect(repository.private).to.equal(false);
});
