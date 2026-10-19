// Synthetic service owned by this example; derived from its OpenAPI contract.
// No third-party owner service or vendor account is represented.
const http=require('http');
http.createServer((req,res)=>{
 const reply=(code,body,headers={})=>{res.writeHead(code,{'Content-Type':'application/json',...headers});res.end(body);};
 if(req.method!=='GET')return reply(501,'{"error":"unmodeled operation"}');
 if(req.headers.authorization!=='Bearer synthetic-health-key')return reply(401,'{"private":"DO_NOT_PERSIST_HEALTH_DETAIL"}');
 switch(req.url){
 case '/health': return reply(200,'{"private":"DO_NOT_PERSIST_HEALTH_DETAIL"}');
 case '/failure': return reply(503,'{"private":"DO_NOT_PERSIST_HEALTH_DETAIL"}');
 case '/denied': return reply(403,'{"private":"DO_NOT_PERSIST_HEALTH_DETAIL"}');
 case '/redirect': return reply(302,'{}',{Location:'/must-not-follow'});
 case '/oversized': return reply(200,'x'.repeat(262145));
 default:return reply(501,'{"error":"unmodeled operation"}');
 }
}).listen(process.env.PORT||49645);
