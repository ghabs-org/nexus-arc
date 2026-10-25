// Synthetic native handshake impostors; never a live workspace or credential.
const http=require('http');let mode='html',calls=[];
const modes=['html','redirect','oversized','forged','bootstrap'];
http.createServer((req,res)=>{
 const json=(code,value)=>{res.writeHead(code,{'Content-Type':'application/json'});res.end(JSON.stringify(value));};
 if(req.method==='POST'&&req.url==='/fixture/mode'){
  if(!modes.includes(req.body?.mode))return json(400,{error:'invalid_fixture'});
  mode=req.body.mode;calls=[];return json(200,{mode});
 }
 if(req.method==='GET'&&req.url==='/fixture/calls')return json(200,{calls});
 // Record header presence only: a launcher must send a fresh challenge and never a cookie or bearer.
 calls.push({path:req.url,challenge:req.headers['x-nexus-desktop-challenge']||'',cookie:!!req.headers.cookie,authorization:!!req.headers.authorization});
 if(req.method!=='GET'||req.url!=='/desktop/handshake')return json(501,{error:'unsupported_fixture_operation'});
 if(mode==='redirect'){res.writeHead(302,{Location:'/must-not-follow'});return res.end();}
 if(mode==='oversized'){res.writeHead(200);return res.end('x'.repeat(4097));}
 if(mode==='forged')return json(200,{proof:'0'.repeat(64)});
 // The unauthenticated bootstrap shape the previous launcher accepted.
 if(mode==='bootstrap')return json(200,{mode:'desktop',workspace:{path:'/expected'},token:'x'.repeat(32)});
 res.writeHead(200,{'Content-Type':'text/html'});res.end('<h1>unexpected service</h1>');
}).listen(process.env.PORT||49673);
