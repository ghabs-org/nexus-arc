// Contract: A2A 1.0/0.3 Send configuration; callback is delivered before Send ack.
const http=require('http');
const origin=process.env.NEXUS_A2A_RECEIVER_FIXTURE_ORIGIN||'http://127.0.0.1:49652';
http.createServer(async(req,res)=>{
 const reply=(code,value)=>{res.writeHead(code,{'Content-Type':'application/json'});res.end(JSON.stringify(value));};
 if(req.headers.authorization!=='Bearer synthetic-receiver-peer-token')return reply(401,{});
 if(req.method==='GET'&&req.url==='/card')return reply(200,{name:'Independent callback producer',protocolVersion:'0.3.0',preferredTransport:'JSONRPC',url:origin+'/agent/rpc',supportedInterfaces:[{url:origin+'/agent/rpc',protocolBinding:'JSONRPC',protocolVersion:'1.0'}],capabilities:{pushNotifications:true},defaultInputModes:['text/plain'],defaultOutputModes:['text/plain']});
 if(req.method!=='POST'||req.url!=='/rpc')return reply(501,{});
 let body=req.body;try{if(typeof body==='string')body=JSON.parse(body);}catch{return reply(400,{});}
 const legacy=req.headers['a2a-version']==='0.3',send=['SendMessage','message/send'].includes(body?.method);
 if(!send&&!['GetTask','tasks/get'].includes(body?.method))return reply(501,{});
 if(send){
  const config=body.params?.configuration?.[legacy?'pushNotificationConfig':'taskPushNotificationConfig'];
  if(!config||(legacy?config.authentication?.schemes?.[0]:config.authentication?.scheme)!=='Bearer')return reply(400,{});
  const target=new URL(config.url);
  // Synthetic fixture may call loopback only; never forwards credentials elsewhere.
  if(target.protocol!=='http:'||target.hostname!=='127.0.0.1'||!target.pathname.startsWith('/api/a2a-callbacks/'))return reply(400,{});
  const payload={task:{id:'callback-task',contextId:'callback-context',status:{state:'TASK_STATE_COMPLETED'},artifacts:[{artifactId:'untrusted',parts:[{text:'Callback hint only'}]}]}};
  try{
   const code=await new Promise((resolve,reject)=>{const call=http.request(target,{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+config.authentication.credentials,'X-Nexus-Delivery-ID':'synthetic-early-receipt'}},response=>{response.resume();response.on('end',()=>resolve(response.statusCode));});call.setTimeout(5000,()=>call.destroy(new Error('timeout')));call.on('error',reject);call.end(JSON.stringify(payload));});
   if(code!==202)return reply(502,{error:'Callback not acknowledged'});
  }catch{return reply(502,{error:'Callback unreachable'});}
 }
 const task={id:'callback-task',contextId:'callback-context',status:{state:legacy?(send?'input-required':'completed'):(send?'TASK_STATE_INPUT_REQUIRED':'TASK_STATE_COMPLETED')},artifacts:send?[]:[{artifactId:'authoritative',parts:[{text:'Verified callback recovery',...(legacy?{kind:'text'}:{})}]}],...(legacy?{kind:'task'}:{})};
 return reply(200,{jsonrpc:'2.0',id:body.id,result:legacy||!send?task:{task}});
}).listen(process.env.PORT||49653);
