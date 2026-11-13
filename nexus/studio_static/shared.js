"use strict";

let snapshotGeneration=0,snapshotController=null,snapshotExpiryTimer=null;
function clearSharedSnapshot(message){
  snapshotGeneration++;
  const copy=document.getElementById("shared-continue"),note=document.getElementById("shared-copy-note");
  if(copy){copy.hidden=true;copy.onclick=null;copy.disabled=false;}
  if(note)note.hidden=true;
  snapshotController?.abort();
  clearTimeout(snapshotExpiryTimer);
  document.getElementById("shared-title").textContent="Shared conversation";
  document.getElementById("shared-messages").replaceChildren();
  document.getElementById("shared-status").textContent=message;
  return snapshotGeneration;
}
async function loadSharedSnapshot(){
  const generation=clearSharedSnapshot("Loading read-only snapshot…");
  const current=()=>generation===snapshotGeneration;
  const status=document.getElementById("shared-status");
  snapshotController=new AbortController();
  const signal=snapshotController.signal;
  try{
    const parts=location.hash.slice(1).split("/");
    if(parts.length!==2||!["private","public"].includes(parts[0])||!parts[1])throw new Error("Invalid snapshot link");
    const endpoint=(parts[0]==="public"?"/api/public-shares/":"/api/studio/shares/")+encodeURIComponent(parts[1]);
    const options={credentials:parts[0]==="private"?"same-origin":"omit",cache:"no-store",signal};
    const response=await fetch(endpoint,options);
    if(!current())return;
    if(response.status===401){status.textContent="Sign in to Nexus, then reopen this private snapshot.";return;}
    if(!response.ok)throw new Error("Snapshot unavailable, expired or revoked");
    const share=await response.json();
    if(!current())return;
    const expires=Date.parse(share.expires_at);
    if(!Number.isFinite(expires)||expires<=Date.now())throw new Error("Snapshot unavailable, expired or revoked");
    const expire=()=>{
      if(!current())return;
      const remaining=expires-Date.now();
      if(remaining<=0){clearSharedSnapshot("Snapshot unavailable, expired or revoked");return;}
      snapshotExpiryTimer=setTimeout(expire,Math.min(remaining,2147483647));
    };
    document.getElementById("shared-title").textContent=share.title;document.title=share.title+" · Nexus Studio";
    status.textContent="Read-only snapshot · expires "+new Date(expires).toLocaleString();
    const root=document.getElementById("shared-messages");
    for(const artifact of share.artifacts||[]){
      const button=document.createElement("button");
      button.type="button";button.className="secondary";button.textContent="Download "+artifact.filename;
      button.onclick=async()=>{
        if(!current())return;
        if(Date.now()>=expires){expire();return;}
        button.disabled=true;
        try{
          const response=await fetch(endpoint+"/artifacts/"+encodeURIComponent(artifact.id),options);
          if(!current())return;
          if(!response.ok)throw new Error("File unavailable; snapshot may have expired or been revoked");
          const file=await response.json();
          if(!current())return;
          if(Date.now()>=expires){expire();return;}
          const bytes=Uint8Array.from(atob(file.content_base64),c=>c.charCodeAt(0));
          const url=URL.createObjectURL(new Blob([bytes],{type:"application/octet-stream"})),a=document.createElement("a");
          a.href=url;a.download=file.filename;a.click();
          setTimeout(()=>URL.revokeObjectURL(url),1000);
        }catch(error){if(current()&&error.name!=="AbortError")status.textContent=error.message;}
        finally{if(current())button.disabled=false;}
      };
      root.append(button);
    }
    for(const message of share.messages){
      const article=document.createElement("article"),heading=document.createElement("h2"),body=document.createElement("div");
      heading.textContent=message.label||(message.role==="user"?"User":"Assistant");
      body.className="message-body";
      renderContent(body,message.content);
      article.append(heading,body);root.append(article);
    }
    const copy=document.getElementById("shared-continue"),note=document.getElementById("shared-copy-note");
    if(copy){
      copy.hidden=false;if(note)note.hidden=false;
      let requestId=null;
      copy.onclick=async()=>{
        if(!current()||copy.disabled)return;
        copy.disabled=true;
        try{
          const auth=await fetch("/api/bootstrap",{credentials:"same-origin",cache:"no-store",signal});
          if(!current())return;
          if(!auth.ok)throw new Error("Sign in to Studio, then reopen this snapshot to continue privately.");
          const bootstrap=await auth.json();
          if(!current())return;
          requestId ||= crypto.randomUUID();
          const response=await fetch("/api/studio/shares/continue",{
            method:"POST",credentials:"same-origin",cache:"no-store",signal,
            headers:{"Content-Type":"application/json","X-Nexus-Studio-Token":bootstrap.token},
            body:JSON.stringify({snapshot:parts[1],visibility:parts[0],request_id:requestId})
          });
          if(!current())return;
          if(!response.ok)throw new Error("Could not copy snapshot. Check sign-in, access, expiry and file availability.");
          const result=await response.json();
          if(current())location.assign("/#chat/"+encodeURIComponent(result.session_id));
        }catch(error){if(current()&&error.name!=="AbortError")status.textContent=error.message;}
        finally{if(current())copy.disabled=false;}
      };
    }
    expire();
  }catch(error){
    if(current()&&error.name!=="AbortError")clearSharedSnapshot(error.message);
  }
}
window.addEventListener("hashchange",loadSharedSnapshot);
window.addEventListener("pageshow",event=>{if(event.persisted)loadSharedSnapshot();});
document.addEventListener("visibilitychange",()=>{if(!document.hidden)loadSharedSnapshot();});
loadSharedSnapshot();
