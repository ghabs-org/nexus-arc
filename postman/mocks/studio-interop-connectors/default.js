// Synthetic service for the repository-owned OpenAPI workflow contract.
// It represents no vendor account and returns 501 for unmodeled operations.
const http=require('http');
let reads=0,writes=0;
http.createServer((req,res)=>{
 const reply=(status,body)=>{res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(body));};
 const url=new URL(req.url,'http://localhost');
 if(req.method==='GET'&&url.pathname==='/stats')return reply(200,{reads,writes});
 if(req.method==='GET'&&url.pathname==='/lookup'){reads++;return reply(200,{evidence:'Fixture',id:url.searchParams.get('id')});}
 if(req.method==='POST'&&url.pathname==='/record'){
  let bytes='';req.on('data',chunk=>{bytes+=chunk;if(bytes.length>64000)req.destroy();});
  req.on('end',()=>{let body;try{body=JSON.parse(bytes);}catch{return reply(400,{error:'Invalid JSON'});}
   writes++;if(body.reply==='fail')return reply(503,{error:'Synthetic unavailable'});
   return reply(200,{saved:true,reply:body.reply});
  });return;
 }
 reply(501,{error:'Unmodeled operation'});
}).listen(process.env.PORT||49649);
