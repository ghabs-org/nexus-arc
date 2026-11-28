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
const ROUTE_RULES={static_weights:"standard scoring",fitted_weights:"learned scoring weights",bandit_exploit:"past results",bandit_explore:"trying an alternative",provider_filter:"only your ready providers",caller_catalog:"Studio's model list",routing_policy:"task policy",quota_penalty:"low quota penalised",model_cooldown:"cooling-down models skipped",confidence_gate:"low-confidence fallback"};
function routeLabel(value){const text=String(value||"").replace(/_/g," ");return text.charAt(0).toUpperCase()+text.slice(1);}
function routeTaskText(explain){
  if(!explain?.task)return "";
  const parts=[routeLabel(explain.task)];
  if(explain.complexity)parts.push(`${explain.complexity} complexity`);
  if(Number.isFinite(explain.confidence))parts.push(`${Math.round(explain.confidence*100)}% sure`);
  return parts.join(" · ");
}
function routeInfoHtml(run){
  const explain=run.router_explain;let head="";
  if(run.router_decision_id){
    const model=[run.provider,run.model].filter(Boolean).map(escapeHtml).join(" · ");
    const task=routeTaskText(explain);
    head=`<span class="route-badge">Auto</span>${model?`<span class="route-model">${model}</span>`:""}${task?`<span class="route-task" title="How the router read this request">${escapeHtml(task)}</span>`:""}`;
  }else if(run.router_error)head=`<span class="route-badge is-warning">Auto unavailable</span><span>Automatic model selection unavailable · ${run.status==="completed"?"Answered by":"Fallback provider:"} ${escapeHtml(run.provider)}</span>`;
  if(run.fallback_from)head+=`<span class="route-note">switched from ${escapeHtml(run.fallback_from)} after a usage or readiness problem</span>`;
  if(!head)return "";
  let actions="";
  if(run.router_decision_id&&run.status==="completed"){
    const id=escapeHtml(run.id);
    const current=explain?.task&&ROUTE_TASKS.includes(explain.task)?explain.task:"";
    actions=run.router_feedback?`<p class="route-feedback-done" role="status">Thanks, your feedback went to the router (${escapeHtml(run.router_feedback.verdict==="correct"?"good pick":"wrong pick")}).</p>`
      :`<div class="route-feedback" role="group" aria-label="Was this the right model?"><span class="route-feedback-label">Right model?</span><button class="secondary" data-route-feedback="correct" data-run-id="${id}">Good pick</button><button class="secondary" data-route-feedback="too_cheap" data-run-id="${id}">Needed stronger</button><button class="secondary" data-route-feedback="too_powerful" data-run-id="${id}">Lighter would do</button><label class="route-task-fix">Task<select data-route-task="${id}" data-current="${current}" aria-label="Correct the task type">${current?"":`<option value="">Pick…</option>`}${ROUTE_TASKS.map(task=>`<option value="${task}"${task===current?" selected":""}>${routeLabel(task)}${task===current?" (router)":""}</option>`).join("")}</select></label></div>`;
  }
  return `<p class="route-summary">${head}</p>${routeWhyHtml(explain)}${actions}`;
}
function routeWhyHtml(explain){
  if(!explain||!Array.isArray(explain.candidates)||!explain.candidates.length)return "";
  const facts=[];
  if(explain.task)facts.push(["Task",`${routeLabel(explain.task)}${Number.isFinite(explain.confidence)?` (${Math.round(explain.confidence*100)}% confidence)`:""}`]);
  if(explain.complexity)facts.push(["Complexity",routeLabel(explain.complexity)]);
  if(explain.classifier)facts.push(["Classifier",routeLabel(explain.classifier)]);
  if(explain.policy)facts.push(["Policy",explain.policy]);
  const dl=facts.length?`<dl class="route-facts">${facts.map(([k,v])=>`<dt>${k}</dt><dd>${escapeHtml(v)}</dd>`).join("")}</dl>`:"";
  const rows=explain.candidates.map((item,index)=>`<li${index===0?' class="is-picked"':""}><span class="route-cand-model">${escapeHtml(item.model)}</span><span class="route-cand-score">${Number.isFinite(item.score)?Number(item.score).toFixed(2):"n/a"}</span>${index===0?'<span class="route-cand-tag">picked</span>':""}</li>`).join("");
  const more=Number(explain.considered)-explain.candidates.length;
  const extra=more>0?`<p class="route-more">+${more} more model${more===1?"":"s"} scored lower</p>`:"";
  const rules=(explain.rules||[]).length?`<p class="route-rules"><span>How:</span>${explain.rules.map(rule=>`<span class="route-rule" title="${escapeHtml(rule)}">${escapeHtml(ROUTE_RULES[rule]||routeLabel(rule))}</span>`).join("")}</p>`:"";
  return `<details class="route-why"><summary>Why this model?</summary><div class="route-why-body">${dl}<p class="route-section">Top candidates</p><ol class="route-candidates">${rows}</ol>${extra}${rules}</div></details>`;
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
  document.addEventListener("change",event=>{const select=event.target.closest?.("select[data-route-task]");if(!select||!select.value||select.value===select.dataset.current)return;sendRouteFeedback(select.dataset.routeTask,{verdict:"wrong",corrected_task:select.value},select);});
}
