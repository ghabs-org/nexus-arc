"use strict";
function renderReleaseNotes(data){
  const notice=document.getElementById("release-notes-notice"),content=document.getElementById("release-notes-content"),versions=document.getElementById("release-notes-versions");
  notice.textContent=data.notice;content.replaceChildren();versions.replaceChildren();
  const index=document.createElement("ul");
  for(const entry of data.entries){
    const item=document.createElement("li"),link=document.createElement("a");
    link.textContent=entry.version;link.href="#"+encodeURIComponent(entry.version);item.append(link);index.append(item);
    const section=document.createElement("section"),heading=document.createElement("h2"),status=document.createElement("p");
    section.id=entry.version;heading.textContent=`${entry.version} — ${entry.title}`;
    status.textContent=`${entry.date||"Date not established"} · ${entry.status}`;section.append(heading,status);
    for(const [key,label] of [["changes","Changes"],["limitations","Recorded limits"],["evidence","Source evidence"]]){
      if(!entry[key]?.length)continue;
      const title=document.createElement("h3"),list=document.createElement("ul");title.textContent=label;
      for(const text of entry[key]){const row=document.createElement("li");row.textContent=text;list.append(row);}
      section.append(title,list);
    }
    content.append(section);
  }
  versions.append(index);
  const selected=document.getElementById(decodeURIComponent(location.hash.slice(1)));
  selected?.scrollIntoView();
}
async function loadReleaseNotes(){
  try{
    const response=await fetch("/release-notes.json",{credentials:"omit",cache:"no-cache"});
    if(!response.ok)throw new Error("unavailable");
    renderReleaseNotes(await response.json());
  }catch{
    document.getElementById("release-notes-notice").textContent="Release history could not be loaded. Retry this page or ask Studio assistant.";
  }
}
loadReleaseNotes();
