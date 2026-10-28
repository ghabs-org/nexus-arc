"use strict";
// Cache only the public application shell. Never intercept auth, API or downloads.
const SHELL_CACHE="nexus-studio-shell-v63";
const SHELL=["/workspace-access.js","/conversation-content.js","/artifact.html","/artifact-viewer.js","/vendor/marked.js","/vendor/purify.js","/content.js","/features.js","/shared.html","/shared.js","/","/studio.css","/studio.js","/quick-build.js","/library.js","/operations.js","/graph.js","/workspace.js","/integrations.js","/workbench.js","/experience.js","/manifest.webmanifest","/favicon.ico","/icon-192.png","/icon-512.png"];
self.addEventListener("install",event=>{event.waitUntil(caches.open(SHELL_CACHE).then(cache=>cache.addAll(SHELL)));});
self.addEventListener("message",event=>{if(event.data?.type==="ACTIVATE_UPDATE")self.skipWaiting();});
self.addEventListener("activate",event=>{event.waitUntil((async()=>{for(const name of await caches.keys())if(name.startsWith("nexus-studio-shell-")&&name!==SHELL_CACHE)await caches.delete(name);await self.clients.claim();})());});
self.addEventListener("fetch",event=>{
  const url=new URL(event.request.url);
  if(event.request.method!=="GET"||url.origin!==self.location.origin||url.search||!SHELL.includes(url.pathname))return;
  event.respondWith((async()=>{try{return await fetch(event.request);}catch(error){const cached=await caches.match(url.pathname,{cacheName:SHELL_CACHE});if(cached)return cached;throw error;}})());
});
