// Synthetic peer derived from the local consumer contract and A2A Part/Artifact.
const http=require('http');
const origin=process.env.NEXUS_A2A_FIXTURE_ORIGIN||'http://127.0.0.1:49650';
http.createServer((req,res)=>{
  const reply=(status,value,type='application/json')=>{res.writeHead(status,{'Content-Type':type});res.end(type==='application/json'?JSON.stringify(value):value);};
  if(req.headers.authorization!=='Bearer synthetic-client-token')return reply(401,{});
  if(req.method==='GET'&&['/card','/url-card','/failure-card'].includes(req.url)){
    const suffix=req.url.replace('card','rpc');
    return reply(200,{name:'Synthetic rich peer',protocolVersion:'0.3.0',preferredTransport:'JSONRPC',url:origin+'/agent'+suffix,supportedInterfaces:[{url:origin+'/agent'+suffix,protocolBinding:'JSONRPC',protocolVersion:'1.0'}],defaultInputModes:['text/plain','application/json','application/octet-stream'],defaultOutputModes:['text/plain','application/json','application/octet-stream'],capabilities:{streaming:false}});
  }
  if(req.method==='GET'&&req.url==='/file')return reply(200,Buffer.from('Returned file evidence'),'application/octet-stream');
  if(req.method!=='POST'||!['/rpc','/url-rpc','/failure-rpc'].includes(req.url))return reply(501,{});
  let body=req.body;try{if(typeof body==='string')body=JSON.parse(body);}catch{return reply(400,{});}
  if(req.url==='/failure-rpc')return reply(502,'<html>PRIVATE_RECEIVER_ERROR</html>','text/html');
  const legacy=req.headers['a2a-version']==='0.3';
  if(!['SendMessage','GetTask','message/send','tasks/get'].includes(body?.method))return reply(501,{});
  if(body.method==='SendMessage'||body.method==='message/send'){
    const parts=body.params?.message?.parts;
    if(!Array.isArray(parts)||parts.length!==3||parts[1].data?.city!=='Rome'||!(parts[2].raw||parts[2].file?.bytes))return reply(400,{});
  }
  const encoded=Buffer.from('Returned file evidence').toString('base64'),url=origin+'/agent/file';
  const file=legacy?{kind:'file',file:{name:'report.txt',...(req.url==='/url-rpc'?{uri:url}:{bytes:encoded})}}:{filename:'report.txt',...(req.url==='/url-rpc'?{url}:{raw:encoded})};
  const task={id:'synthetic-remote-task',contextId:'synthetic-context',status:{state:legacy?'completed':'TASK_STATE_COMPLETED'},artifacts:[{artifactId:'report',parts:[{text:'Remote report',...(legacy?{kind:'text'}:{})},{data:{count:3},...(legacy?{kind:'data'}:{})},file]}],...(legacy?{kind:'task'}:{})};
  const result=legacy||['GetTask','tasks/get'].includes(body.method)?task:{task};
  reply(200,{jsonrpc:'2.0',id:body.id,result});
}).listen(process.env.PORT||49651);
