// Local receiver contract fixture; no external messages or vendor credentials.
const http=require('http');
http.createServer((req,res)=>{
  const reply=(status,value='',headers={})=>{res.writeHead(status,headers);res.end(value);};
  if(req.method!=='POST'||!['/callback','/failure','/redirect'].includes(req.url))return reply(501);
  if(req.headers.authorization!=='Bearer synthetic-callback-secret')return reply(401);
  let body=req.body;
  try{if(typeof body==='string')body=JSON.parse(body);}catch{return reply(400);}
  if(req.headers['content-type']!=='application/a2a+json'||!req.headers['x-nexus-delivery-id']||Object.keys(body||{}).join()!=='task'||!body.task.id||!Array.isArray(body.task.artifacts))return reply(400);
  for(const artifact of body.task.artifacts)for(const part of artifact.parts||[]){if(['text','raw','url','data'].filter(key=>key in part).length!==1)return reply(400);}
  if(req.url==='/failure')return reply(502,'<html>synthetic proxy failure</html>',{'Content-Type':'text/html'});
  if(req.url==='/redirect')return reply(302,'',{'Location':'/must-not-follow'});
  reply(204);
}).listen(process.env.PORT||49649);
