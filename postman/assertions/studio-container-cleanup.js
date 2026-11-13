const report=pm.response.json();
pm.test("Cleanup returns a confirmed metadata report",()=>{
  pm.response.to.have.status(200);
  pm.expect(report.checked_at).to.be.a("string");
  pm.expect(Number.isFinite(Date.parse(report.checked_at))).to.equal(true);
  pm.expect(Number.isInteger(report.pending)&&report.pending>=0).to.equal(true);
  pm.expect(report.items).to.be.an("array");
  pm.expect(report.items.length).to.be.at.most(32);
});
pm.test("Each result preserves its actual cleanup outcome",()=>{
  const statuses=["removed","owner_active","other_host","route_changed","ownership_mismatch","awaiting_creation","invalid_record","unconfirmed"];
  for(const item of report.items){
    pm.expect(item.name).to.be.a("string");
    pm.expect(statuses).to.include(item.status);
    pm.expect(Object.keys(item).sort()).to.deep.equal(["name","status"]);
  }
});
pm.test("Cleanup never returns code, credentials or document payloads",()=>{
  for(const key of ["code","credentials","environment","files","content_base64"]){
    pm.expect(report).not.to.have.property(key);
  }
});
