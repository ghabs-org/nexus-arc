"use strict";
function renderRetrievalNotice(node,notice){
  let note=node.querySelector(".retrieval-notice");
  if(!note){note=document.createElement("p");note.className="retrieval-notice callout";note.setAttribute("role","note");node.append(note);}
  note.textContent=typeof notice==="string"?notice:"";note.hidden=!note.textContent;
}
function renderStructuredContent(node,parts=[]){
  node.querySelector(".structured-content")?.remove();
  if(!Array.isArray(parts)||!parts.length)return;
  const section=document.createElement("section");section.className="structured-content";section.setAttribute("aria-label","Additional response content");
  for(const part of parts){
    const content=document.createElement("div");
    if(part.type==="markdown")renderContent(content,part.text);
    else if(part.type==="text")content.textContent=part.text;
    else if(part.type==="json"){const pre=document.createElement("pre");pre.textContent=JSON.stringify(part.data,null,2);content.append(pre);}
    else if(part.type==="artifact"&&Number.isSafeInteger(part.revision)&&part.revision>0){
      const link=document.createElement("a");link.href=`/artifact.html?id=${encodeURIComponent(part.artifact_id)}&revision=${part.revision}&page=1`;link.textContent=part.filename||"Open response artifact";link.target="_blank";link.rel="noopener noreferrer";content.append(link);
    }else continue;
    section.append(content);
  }
  node.append(section);
}
function renderRunMessage(node,run){
  const body=node.querySelector(".message-body"),text=run.output||(["running","queued"].includes(run.status)?"Working…":"");
  if(body.dataset.renderedText!==text){renderContent(body,text);body.dataset.renderedText=text;}
  showCitations(node,run.citations||[]);
  renderRetrievalNotice(node,run.retrieval_notice);
  const parts=JSON.stringify(run.structured_content||[]);
  if(node.dataset.renderedParts!==parts){renderStructuredContent(node,run.structured_content||[]);node.dataset.renderedParts=parts;}
  let status=node.querySelector(".stream-status");
  if(!status){status=document.createElement("p");status.className="stream-status";status.setAttribute("role","status");node.append(status);}
  status.textContent=run.status==="running"?(run.stream?.status==="streaming"?"Receiving response…":"Working…"):run.status==="queued"?"Queued":run.error||"";
  if(run.stream?.truncated&&run.status==="running")status.textContent="Live preview limit reached. The completed response will appear here.";
  status.hidden=!status.textContent;
}
