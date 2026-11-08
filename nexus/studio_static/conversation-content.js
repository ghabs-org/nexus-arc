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
function providerSigninHelp(run){
  if(run.status!=="failed"||typeof state==="undefined"||state.mode==="team")return "";
  if(!/not logged in|not authenticated|please run \/login/i.test(String(run.error||"")))return "";
  const provider=(state.providers||[]).find(item=>item.id===run.provider);
  if(!provider||provider.kind!=="cli")return "";
  const signin=typeof desktopProviderSignin==="function"?desktopProviderSignin(provider):"";
  return `<p>${escapeHtml(provider.name)} needs its own local sign-in. Complete it in the provider’s terminal and browser, then retry your message. Do not send /login as a Studio chat message.</p>${signin||'<a class="button-link" href="#providers">Open Providers & models</a>'}`;
}
function renderRunMessage(node,run){
  const pending=["running","queued"].includes(run.status);
  const body=node.querySelector(".message-body"),text=run.output||"";
  node.dataset.pending=String(pending);
  if(body.dataset.renderedText!==text){renderContent(body,text);body.dataset.renderedText=text;}
  body.hidden=!text;
  showCitations(node,run.citations||[]);
  renderRetrievalNotice(node,run.retrieval_notice);
  const parts=JSON.stringify(run.structured_content||[]);
  if(node.dataset.renderedParts!==parts){renderStructuredContent(node,run.structured_content||[]);node.dataset.renderedParts=parts;}
  let status=node.querySelector(".stream-status");
  if(!status){status=document.createElement("p");status.className="stream-status";status.setAttribute("role","status");node.append(status);}
  status.className=`stream-status${pending?" is-loading":""}`;
  status.textContent=run.status==="running"?(run.stream?.status==="streaming"?"Receiving response…":"Working…"):run.status==="queued"?"Queued":run.error===text?"":run.error||"";
  if(run.stream?.truncated&&run.status==="running")status.textContent="Live preview limit reached. The completed response will appear here.";
  status.hidden=!status.textContent;
  const help=providerSigninHelp(run);
  let setup=node.querySelector(".provider-signin-help");
  if(help){
    if(!setup){setup=document.createElement("div");setup.className="provider-signin-help callout";setup.setAttribute("role","note");node.append(setup);}
    setup.innerHTML=help;
  }else setup?.remove();
  renderRouteInfo(node,run);
}
const ROUTE_TASKS=["coding","code_review","reasoning","summarization","fast_utility","long_context","vision","general_chat"];
function routeInfoHtml(run){
  const bits=[];
  if(run.router_decision_id)bits.push(`Auto-routed to ${escapeHtml(run.provider)}${run.model?` · ${escapeHtml(run.model)}`:""}`);
  else if(run.router_error)bits.push(`Model router unavailable, answered by ${escapeHtml(run.provider)}`);
  if(run.fallback_from)bits.push(`switched from ${escapeHtml(run.fallback_from)} after a usage or readiness problem`);
  if(!bits.length)return "";
  let actions="";
  if(run.router_decision_id&&run.status==="completed"){
    const id=escapeHtml(run.id);
    actions=run.router_feedback?`<p class="route-feedback-done" role="status">Thanks, your feedback went to the router (${escapeHtml(run.router_feedback.verdict==="correct"?"good pick":"wrong pick")}).</p>`
      :`<div class="route-feedback" role="group" aria-label="Was this the right model?"><button class="secondary" data-route-feedback="correct" data-run-id="${id}">Good pick</button><button class="secondary" data-route-feedback="too_cheap" data-run-id="${id}">Needed a stronger model</button><button class="secondary" data-route-feedback="too_powerful" data-run-id="${id}">A lighter model would do</button><label>Wrong kind of task?<select data-route-task="${id}"><option value="">It was…</option>${ROUTE_TASKS.map(task=>`<option value="${task}">${task.replace("_"," ")}</option>`).join("")}</select></label></div>`;
  }
  return `<p class="route-summary">${bits.join(" · ")}</p>${actions}`;
}
function renderRouteInfo(node,run){
  const html=routeInfoHtml(run);let info=node.querySelector(".route-info");
  if(!html){info?.remove();return;}
  if(!info){info=document.createElement("div");info.className="route-info";node.append(info);}
  if(info.dataset.rendered!==html){info.innerHTML=html;info.dataset.rendered=html;}
}
async function sendRouteFeedback(runId,body,host){
  try{const result=await api("/api/studio/router-feedback",{run_id:runId,...body});host.closest(".route-info").innerHTML=routeInfoHtml({...result,router_decision_id:true,status:"completed",provider:"",fallback_from:null})||"";}
  catch(error){if(typeof notify==="function")notify(error.message||String(error));}
}
if(typeof document!=="undefined"&&typeof document.addEventListener==="function"){
  document.addEventListener("click",event=>{const button=event.target.closest?.("[data-route-feedback]");if(!button)return;const verdict=button.dataset.routeFeedback;sendRouteFeedback(button.dataset.runId,verdict==="correct"?{verdict:"correct"}:{verdict:"wrong",model_verdict:verdict},button);});
  document.addEventListener("change",event=>{const select=event.target.closest?.("select[data-route-task]");if(!select||!select.value)return;sendRouteFeedback(select.dataset.routeTask,{verdict:"wrong",corrected_task:select.value},select);});
}
