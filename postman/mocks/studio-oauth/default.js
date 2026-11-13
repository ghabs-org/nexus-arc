// Synthetic OAuth token service, derived from the local token contract.
// No vendor or live-account claim; discovery is exercised by separate HTTP fixtures.
const http=require('http');
http.createServer((req,res)=>{
 const reply=(status,value,type='application/json',headers={})=>{res.writeHead(status,{'Content-Type':type,...headers});res.end(type==='application/json'?JSON.stringify(value):value);};
 if(req.method!=='POST'||req.url!=='/token')return reply(501,{error:'Unmodeled OAuth operation'});
 const raw=req.body;const form=typeof raw==='string'?Object.fromEntries(new URLSearchParams(raw)):raw||{};
 if(!form.resource||form.client_id!=='synthetic-client')return reply(400,{error:'invalid_request'});
 if(form.code==='failure')return reply(400,{error:'invalid_grant',error_description:'DO_NOT_LEAK_PROVIDER_DETAIL'});
 if(form.code==='html')return reply(502,'<html>DO_NOT_LEAK_PROVIDER_DETAIL</html>','text/html');
 if(form.code==='redirect')return reply(302,{},'application/json',{Location:'/must-not-follow'});
 if(form.code==='oversized')return reply(200,'x'.repeat(65537),'text/plain');
 if(form.grant_type==='authorization_code'&&(!form.code_verifier||!form.redirect_uri))return reply(400,{error:'invalid_request'});
 if(!['authorization_code','refresh_token'].includes(form.grant_type))return reply(501,{error:'unsupported_grant_type'});
 reply(200,{access_token:'synthetic-postman-access',refresh_token:form.grant_type==='refresh_token'?'rotated-refresh':'synthetic-refresh',token_type:'Bearer',expires_in:3600,scope:'read'});
}).listen(process.env.PORT||49646);
