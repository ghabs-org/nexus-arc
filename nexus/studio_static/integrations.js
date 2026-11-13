"use strict";
const integrations={items:[],pane:"available"};
function updateIntegrationVisibility(){
  const showing=library.kind==="connectors";
  $("integrations-panel").hidden=!showing;
  $("library-description").hidden=showing;
  $("integration-available").hidden=integrations.pane!=="available";
  $("library-content").hidden=showing&&integrations.pane!=="saved";
  $("library-archived").parentElement.hidden=showing&&integrations.pane!=="saved";
  $("integration-saved-count").textContent=library.items.filter(item=>!item.archived).length;
  document.querySelectorAll("[data-integration-pane]").forEach(button=>button.setAttribute("aria-pressed",String(button.dataset.integrationPane===integrations.pane)));
}
function renderIntegrationCatalog(){
  const query=$("integration-search").value.trim().toLocaleLowerCase(),category=$("integration-category").value;
  const items=integrations.items.filter(item=>(!category||item.category===category)&&[item.name,item.summary,...item.capabilities].join(" ").toLocaleLowerCase().includes(query));
  $("integration-count").textContent=`${items.length} of ${integrations.items.length} integrations`;
  $("integration-list").innerHTML=items.length?items.map(item=>`<article class="integration-row"><div class="integration-copy"><h3>${escapeHtml(item.name)}<span>${escapeHtml(item.category)}</span></h3><p>${escapeHtml(item.summary)}</p></div><div class="integration-actions">${badge(item.status,item.studio_support==="studio"||item.active_transport?"ready":"")}${item.connection_count?`<small>${item.connection_count} saved · ${item.enabled_count} enabled</small>`:""}<button class="secondary" data-integration-details="${escapeHtml(item.id)}">View capabilities</button>${item.studio_area?`<button class="secondary" data-wb-area="${escapeHtml(item.studio_area)}">${item.studio_area==="storage"?"Open file storage":"Open campaigns"}</button>`:""}${item.studio_support==="studio"?`<button class="secondary" ${item.id==="teams"?"data-integration-create":"data-guide-create"}="${escapeHtml(item.id)}">Set up connection</button>`:""}</div></article>`).join(""):'<p class="integration-empty">No matching integrations. Try another name or category.</p>';
}
async function refreshIntegrationCatalog(){
  updateIntegrationVisibility();
  if(library.kind!=="connectors")return;
  try{
    integrations.items=(await api("/api/integrations")).items;
    const category=$("integration-category").value;
    $("integration-category").innerHTML='<option value="">All categories</option>'+[...new Set(integrations.items.map(item=>item.category))].sort().map(category=>`<option value="${escapeHtml(category)}">${escapeHtml(category)}</option>`).join("");
    $("integration-category").value=category;
    $("integration-error").textContent="";renderIntegrationCatalog();updateIntegrationVisibility();
  }catch(error){$("integration-error").textContent=error.message;}
}
async function refreshArcCapabilities(){
  try{
    const items=(await api("/api/arc-capabilities")).items;
    $("arc-capabilities-content").innerHTML=items.map(item=>`<details class="arc-capability"><summary>${escapeHtml(item.name)}</summary><p>${escapeHtml(item.summary)}</p><p class="workspace-scope">${escapeHtml(item.scope)}</p><button class="secondary" data-wb-area="${escapeHtml(item.studio_area||item.id)}">Open controls</button></details>`).join("");
    $("arc-capabilities-error").textContent="";
  }catch(error){$("arc-capabilities-error").textContent=error.message;}
}
$("integration-search").addEventListener("input",renderIntegrationCatalog);
$("integration-category").addEventListener("change",renderIntegrationCatalog);
document.addEventListener("click",async event=>{
  const button=event.target.closest("button");if(!button)return;
  try{
    if(button.dataset.integrationPane){integrations.pane=button.dataset.integrationPane;updateIntegrationVisibility();}
    const identity=button.dataset.integrationDetails||button.dataset.integrationCreate;
    if(!identity)return;
    const item=integrations.items.find(item=>item.id===identity);if(!item)return;
    if(button.dataset.integrationCreate){await openLibraryEditor(undefined,item);return;}
    showDetail(item.name,`<p>${escapeHtml(item.summary)}</p>${badge(item.status)}<h3>Existing capabilities</h3><ul>${item.capabilities.map(value=>`<li>${escapeHtml(value)}</li>`).join("")}</ul><h3>Studio setup</h3><p>${escapeHtml(item.setup)}</p>${item.studio_support==="studio"?`<p>${item.connection_count} saved connections · ${item.enabled_count} enabled. Account or endpoint access is checked only when you run a connection test.</p>`:'<p class="callout">This entry describes existing ARC support. Browsing it does not connect an account or enable actions.</p>'}`);
  }catch(error){$("integration-error").textContent=error.message;}
});
window.addEventListener("studio-resource-saved",event=>{if(event.detail.kind==="connectors"){integrations.pane="saved";updateIntegrationVisibility();}});
window.addEventListener("hashchange",()=>{if(view()==="workspace")refreshArcCapabilities();});
function initializeIntegrations(){refreshIntegrationCatalog();if(view()==="workspace")refreshArcCapabilities();}
if(state.ready)initializeIntegrations();else window.addEventListener("studio-ready",initializeIntegrations,{once:true});
