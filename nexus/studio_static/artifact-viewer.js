"use strict";
(async()=>{
  const query=new URLSearchParams(location.search),id=query.get("id"),revision=Number(query.get("revision")),page=Number(query.get("page")||1);
  const status=document.getElementById("artifact-status"),root=document.getElementById("artifact-content");
  async function fetchPage(selected){
    const response=await fetch(`/api/catalog/artifacts/${encodeURIComponent(id)}/preview?revision=${revision}&page=${selected}`,{credentials:"same-origin",cache:"no-store"});
    const result=await response.json();
    if(response.status===401)throw new Error("Sign in to Nexus, then reopen this source link.");
    if(!response.ok)throw new Error(result.error||"Document source unavailable");
    return result;
  }
  try{
    if(!id||!Number.isSafeInteger(revision)||revision<1||!Number.isSafeInteger(page)||page<1)throw new Error("Invalid pinned document link");
    const preview=await fetchPage(page);
    document.getElementById("artifact-title").textContent=preview.filename||"Document source";
    status.textContent=`Revision ${revision}`;
    root.replaceChildren(previewContent(preview,{fetchPage}));
    if(!root.querySelector('a[download]')){const download=document.createElement("a");download.className="button-link secondary";download.href=`/api/catalog/artifacts/${encodeURIComponent(id)}/versions/${revision}`;download.textContent="Download this revision";root.append(download);}
  }catch(error){status.textContent=error.message;status.setAttribute("role","alert");}
})();
