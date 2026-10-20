// Synthetic peers from vendor wire contracts; no actual tenant/provider account.
const http=require('http');
http.createServer((req,res)=>{
 const reply=(status,value)=>{res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(value));};
 const body=req.body;
 if(req.url.startsWith('/v1.0/teams/11111111-2222-3333-4444-555555555555/channels/19%3Atest%40thread.tacv2')){
  if(req.headers.authorization!=='Bearer synthetic-teams-token')return reply(401,{});
  if(req.method==='GET')return reply(200,{id:'fixture-message',displayName:'Fixture channel',value:[{id:'fixture-message',body:{content:'Synthetic Teams post'}}]});
  if(['POST','PATCH'].includes(req.method)&&body?.body?.contentType==='text')return reply(200,{id:'fixture-message',body:body.body});
  return reply(501,{});
 }
 if(req.url.startsWith('/items/item')&&req.method==='GET'){
  if(req.headers['x-account']!=='synthetic-openapi-token')return reply(401,{});
  return reply(200,{verified:true});
 }
 if(req.url==='/native'&&req.method==='POST'){
  if(req.headers.authorization!=='Bearer synthetic-image-key'||body?.model!=='gpt-6-luna'||!Array.isArray(body.input)||body.input[0]?.content[1]?.type!=='input_image')return reply(400,{});
  return reply(200,{answers:[{name:'visible',type:'predicate',probability:.9}]});
 }
 return reply(501,{});
}).listen(process.env.PORT||49731);
