"use strict";
function renderContent(node, text) {
  if (!window.marked || !window.DOMPurify) { node.textContent = text; return; }
  node.innerHTML = DOMPurify.sanitize(marked.parse(String(text || ""), {gfm:true, breaks:true}), {
    ALLOWED_TAGS:["p","br","strong","em","del","code","pre","h1","h2","h3","h4","h5","h6","blockquote","ul","ol","li","table","thead","tbody","tr","th","td","hr","a"],
    ALLOWED_ATTR:["href","title"], ALLOW_DATA_ATTR:false
  });
  node.querySelectorAll("a").forEach(link => {
    try { const url = new URL(link.getAttribute("href"), location.href); if(!["http:","https:"].includes(url.protocol)||url.username||url.password)link.removeAttribute("href"); }
    catch { link.removeAttribute("href"); }
    link.rel="noopener noreferrer"; link.target="_blank";
  });
  node.querySelectorAll("table").forEach(table => {
    if (!table.parentElement.classList.contains("table-container")) {
      const wrap = document.createElement("div");
      wrap.className = "table-container";
      table.parentNode.insertBefore(wrap, table);
      wrap.appendChild(table);
    }
  });
  node.querySelectorAll("pre").forEach(pre => {
    const button=document.createElement("button");button.type="button";button.className="secondary code-copy";button.textContent="Copy code";
    button.addEventListener("click",async()=>{try{await navigator.clipboard.writeText(pre.querySelector("code")?.textContent||pre.textContent);button.textContent="Copied";}catch{button.textContent="Select code to copy";if(typeof notify==="function")notify("Copy unavailable; select the code instead.");}});
    pre.appendChild(button);
  });
}
function showCitations(node, citations=[]) {
  const expanded=node.querySelector(".source-list")?.open;node.querySelector(".source-list")?.remove();if(!citations.length)return;
  const details=document.createElement("details");details.className="source-list";
  details.open=!!expanded;
  const title=document.createElement("summary");title.textContent=`${citations.length} ${citations.length===1?"source":"sources"}`;details.append(title);
  citations.forEach((source,index)=>{const link=document.createElement("a");
    if(source.kind==="web"){
      try{const url=new URL(source.url);if(!["http:","https:"].includes(url.protocol)||url.username||url.password)return;link.href=url.href;}catch{return;}
      link.textContent=`[source:${index+1}] ${source.title||source.url}`;link.title=`Retrieved ${source.retrieved_at||""} · ${source.sha256||""}`;
    }else{
      if(typeof source.artifact_id!=="string"||!Number.isInteger(source.artifact_revision)||source.artifact_revision<1||!Number.isInteger(source.page)||source.page<1)return;
      link.textContent=`[source:${index+1}] ${source.filename} · page/section ${source.page} · revision ${source.artifact_revision}`;
      link.href=`/artifact.html?id=${encodeURIComponent(source.artifact_id)}&revision=${source.artifact_revision}&page=${source.page}`;
      link.addEventListener("click",async event=>{
        if(event.ctrlKey||event.metaKey||event.shiftKey||event.altKey||typeof api!=="function"||typeof showDetail!=="function"||typeof $!=="function")return;
        event.preventDefault();
        try{
          const preview=await api(`/api/catalog/artifacts/${encodeURIComponent(source.artifact_id)}/preview?revision=${source.artifact_revision}&page=${source.page}`);
          showDetail(`${source.filename} · revision ${source.artifact_revision}`,"");$("detail-content").replaceChildren(previewContent(preview));
        }catch(error){if(typeof notify==="function")notify(error.message);}
      });}
    link.target="_blank";link.rel="noopener noreferrer";details.append(link);
    appendOriginalFiles(details,source);
  });
  node.append(details);
}
async function requestOriginalFiles(source,selection={}) {
  const run=await api("/api/studio/original-document",{source_run_id:source.source_run_id,source_index:source.source_index,...selection});
  const completed=await pollRun(run.id);
  return JSON.parse(completed.output);
}
async function saveOriginalFile(button,source,selection={},label="Save current original") {
  if(button.disabled)return;
  button.disabled=true;button.textContent="Saving original…";
  try{
    const result=await requestOriginalFiles(source,selection),artifact=result.artifacts?.[0];
    if(!artifact||typeof artifact.id!=="string"||!Number.isInteger(artifact.revision))throw new Error("Original file has no saved snapshot.");
    const saved=document.createElement("a");saved.href=`/artifact.html?id=${encodeURIComponent(artifact.id)}&revision=${artifact.revision}&page=1`;saved.textContent="Open saved original";saved.target="_blank";saved.rel="noopener noreferrer";button.replaceWith(saved);
  }catch(error){button.disabled=false;button.textContent=label;if(typeof notify==="function")notify(error.message);}
}
function appendOriginalFiles(details,source) {
  if(!source.original_document||typeof source.source_run_id!=="string"||!Number.isInteger(source.source_index)||typeof api!=="function"||typeof pollRun!=="function")return;
  const button=document.createElement("button");button.type="button";button.className="secondary";
  if(!source.field_id?.startsWith("c/")){
    button.textContent="Save current original";button.title="Copies the current remote file. Previously cited text stays pinned.";
    button.addEventListener("click",()=>saveOriginalFile(button,source));details.append(button);return;
  }
  button.textContent="Browse attachments";button.title="List files in this cited conversation. Files are saved only when selected.";
  const panel=document.createElement("div");panel.className="source-attachments";panel.setAttribute("aria-live","polite");let loading=false;
  async function load(page=1,offset=0){
    if(loading)return;loading=true;button.disabled=true;button.textContent="Loading attachments…";panel.replaceChildren();
    try{
      const result=await requestOriginalFiles(source,{operation:"list",page,offset});
      if(!Array.isArray(result.attachments))throw new Error("Attachment listing is unavailable.");
      const status=document.createElement("p");status.textContent=`Page ${result.page} of ${result.pages} · ${result.attachments.length} of ${result.attachment_count} files on this page.`;panel.append(status);
      if(!result.attachments.length){const empty=document.createElement("p");empty.textContent="No files on this conversation page.";panel.append(empty);}
      for(const item of result.attachments){
        const row=document.createElement("p"),name=document.createElement("span"),save=document.createElement("button");row.className="source-attachment-row";
        name.textContent=`${item.filename} · message ${item.message_id}`;save.type="button";save.className="secondary";save.textContent="Save file";
        save.addEventListener("click",()=>saveOriginalFile(save,source,{attachment_id:item.attachment_id},"Save file"));row.append(name,save);panel.append(row);
      }
      if(result.unsupported_references){const note=document.createElement("p");note.textContent=`${result.unsupported_references} non-file or split references cannot be downloaded here.`;panel.append(note);}
      const controls=document.createElement("div");controls.className="card-actions";
      const add=(label,nextPage,nextOffset)=>{const navigation=document.createElement("button");navigation.type="button";navigation.className="secondary";navigation.textContent=label;navigation.addEventListener("click",()=>load(nextPage,nextOffset));controls.append(navigation);};
      if(offset>0)add("Earlier files",page,Math.max(0,offset-100));
      if(Number.isInteger(result.next_offset))add("More files",page,result.next_offset);
      if(page>1)add("Previous page",page-1,0);
      if(Number.isInteger(result.next_page))add("Next page",result.next_page,0);
      panel.append(controls);
    }catch(error){panel.textContent=error.message;if(typeof notify==="function")notify(error.message);}
    finally{loading=false;button.disabled=false;button.textContent="Browse attachments";}
  }
  button.addEventListener("click",()=>load());details.append(button,panel);
}
function previewDocument(root, preview, options={}) {
  let current=preview;
  const controls=document.createElement("form");controls.className="document-navigation";controls.setAttribute("aria-label","Document navigation");
  const previous=document.createElement("button"),next=document.createElement("button"),go=document.createElement("button"),input=document.createElement("input"),label=document.createElement("label"),position=document.createElement("span"),status=document.createElement("p"),text=document.createElement("pre"),note=document.createElement("p"),download=document.createElement("a");
  previous.type=next.type="button";previous.className=next.className=go.className="secondary";previous.textContent="Previous";next.textContent="Next";go.type="submit";go.textContent="Go";
  input.type="number";input.min="1";input.step="1";input.required=true;input.style.width="5rem";label.textContent=`${preview.unit||"Page"} `;label.append(input);status.setAttribute("role","status");status.setAttribute("aria-live","polite");
  controls.append(previous,label,position,go,next);root.append(controls,status,text,note,download);
  note.textContent=preview.kind==="office"?"Text sections follow the indexed source order. Printed page layout is not reproduced.":"Text preview. Download the document to inspect page images and layout.";
  download.textContent="Download this revision";download.className="button-link";download.href=`/api/catalog/artifacts/${encodeURIComponent(preview.artifact_id)}/versions/${preview.revision}`;download.setAttribute("download","");
  const fetchPage=options.fetchPage||(typeof api==="function"?(page=>api(`/api/catalog/artifacts/${encodeURIComponent(preview.artifact_id)}/preview?revision=${preview.revision}&page=${page}`)):null);
  function paint(value){
    current=value;input.value=String(value.page||1);input.max=String(value.pages||1);position.textContent=value.pages?` of ${value.pages} · revision ${value.revision}`:` · revision ${value.revision}`;
    previous.disabled=!fetchPage||!value.extraction_available||value.page<=1;next.disabled=!fetchPage||!value.extraction_available||value.page>=value.pages;input.disabled=go.disabled=!fetchPage||!value.extraction_available;
    text.textContent=value.text|| (value.extraction_available?"No extractable text in this source unit.":"");
    status.textContent=value.extraction_available===false?`Preview unavailable. ${value.message||"The optional document extractor is unavailable."}`:value.truncated?"This source unit exceeds the text preview limit. Download this revision to inspect the full content.":"";
  }
  async function navigate(page){
    if(!fetchPage||!Number.isInteger(page)||page<1||page>current.pages){status.textContent="Choose a valid page or section number.";return;}
    previous.disabled=next.disabled=input.disabled=go.disabled=true;status.textContent="Loading source…";
    try{
      const value=await fetchPage(page);
      if(value.artifact_id!==preview.artifact_id||value.revision!==preview.revision||value.page!==page)throw new Error("Source revision changed unexpectedly. Reopen the pinned citation.");
      paint(value);
    }catch(error){paint(current);status.textContent=error.message;}
  }
  controls.addEventListener("submit",event=>{event.preventDefault();navigate(Number(input.value));});previous.addEventListener("click",()=>navigate(current.page-1));next.addEventListener("click",()=>navigate(current.page+1));paint(preview);
}
function previewContent(preview, options={}) {
  const root=document.createElement("div");root.className="rich-preview";
  if(preview.kind==="pdf"||preview.kind==="office")previewDocument(root,preview,options);
  else if(preview.kind==="image"||preview.kind==="audio"){
    const media=document.createElement(preview.kind==="image"?"img":"audio");media.src=`data:${preview.content_type};base64,${preview.data_base64}`;
    if(preview.kind==="audio")media.controls=true;else media.alt="Artifact preview";root.append(media);
  } else if(preview.kind==="html") {
    const frame=document.createElement("iframe");frame.title="Restricted HTML preview";frame.setAttribute("sandbox","");frame.referrerPolicy="no-referrer";
    frame.srcdoc="<!doctype html><meta name='color-scheme' content='light dark'><meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'none'; img-src 'none'; form-action 'none'; base-uri 'none'\">"+DOMPurify.sanitize(preview.text,{FORBID_TAGS:["script","style","link","meta","base","form","input","button","select","textarea","svg","math","iframe","object","embed","img","video","audio"],FORBID_ATTR:["href","src","srcset","style"]});root.append(frame);
  } else if(preview.kind==="csv") {
    const table=document.createElement("table");preview.rows.forEach((row,i)=>{const tr=document.createElement("tr");row.forEach(value=>{const cell=document.createElement(i?"td":"th");cell.textContent=value;tr.append(cell);});table.append(tr);});root.append(table);
  } else if(preview.kind==="mermaid") {
    root.textContent="Rendering diagram…";
    if(preview.text.length>10000){root.textContent="Diagram exceeds preview limit. Download the source to inspect it.";return root;}
    import("/vendor/mermaid/mermaid.esm.min.mjs").then(async module=>{
      const mermaid=module.default;mermaid.initialize({startOnLoad:false,securityLevel:"strict",htmlLabels:false,look:"classic",maxTextSize:10000,maxEdges:200,flowchart:{htmlLabels:false},theme:"neutral",suppressErrorRendering:true,secure:["securityLevel","htmlLabels","startOnLoad","maxTextSize","maxEdges","flowchart","look","themeCSS"]});
      const rendered=await mermaid.render("diagram-"+crypto.randomUUID(),preview.text);
      const fragment=DOMPurify.sanitize(rendered.svg,{RETURN_DOM_FRAGMENT:true,USE_PROFILES:{svg:true},FORBID_TAGS:["foreignObject","a","image","script","style","animate","set","feImage"],FORBID_ATTR:["style","href","xlink:href"],ALLOW_DATA_ATTR:false});
      fragment.querySelectorAll("*").forEach(element=>Array.from(element.attributes).forEach(attribute=>{if(/[\\]|url\s*\(/i.test(attribute.value)&&!/^url\(#[\w-]+\)$/.test(attribute.value))element.removeAttribute(attribute.name);}));root.replaceChildren(fragment);
    }).catch(()=>{root.textContent="Diagram could not be rendered. Its source is available below.";const pre=document.createElement("pre");pre.textContent=preview.text;root.append(pre);});
  } else if(preview.kind==="markdown")renderContent(root,preview.text);
  else {const pre=document.createElement("pre");pre.textContent=preview.text||preview.message||"No preview";root.append(pre);}
  return root;
}
