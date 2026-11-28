// Public Slack chat.postMessage/chat.update shapes; synthetic local data only.
const http=require('http');
let calls=[];
http.createServer((req,res)=>{
  const reply=(status,value,headers={})=>{res.writeHead(status,{'Content-Type':'application/json',...headers});res.end(JSON.stringify(value));};
  if(req.method==='GET'&&req.url==='/fixture/calls')return reply(200,{calls});
  const url=new URL(req.url,'http://fixture');
  if(req.method==='GET'&&(url.pathname==='/api/files.info'||url.pathname.startsWith('/files-pri/'))){
    if(req.headers.authorization!=='Bearer synthetic-slack-token')return reply(401,{ok:false,error:'invalid_auth'});
    const id=url.searchParams.get('file')||url.pathname.split('/')[2];
    calls.push({method:url.pathname,file:id});
    const bytes=id==='FIMAGE1'?Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jVv8AAAAASUVORK5CYII=','base64'):Buffer.from('private Slack evidence');
    if(url.pathname==='/api/files.info'){
      if(id==='FERROR')return reply(200,{ok:false,error:'PRIVATE_PROVIDER_DETAIL'});
      if(id==='FHTML'){res.writeHead(500,{'Content-Type':'text/html'});return res.end('<h1>PRIVATE_PROVIDER_DETAIL</h1>');}
      return reply(200,{ok:true,file:{id,name:id==='FIMAGE1'?'pixel.png':'evidence.txt',mode:'hosted',is_external:false,size:id==='FBIG'?16777217:bytes.length,shares:{private:{CSELECTED:[{ts:'1700000001.000001'}]}},url_private_download:`https://files.slack.com/files-pri/${id}/download/source`}});
    }
    if(id==='FREDIRECT'){res.writeHead(302,{Location:'https://evil.invalid/leak'});return res.end();}
    res.writeHead(200,{'Content-Type':'application/octet-stream'});
    return res.end(id==='FTRUNC'?bytes.subarray(0,3):bytes);
  }
  if(req.method!=='POST'||!['/api/chat.postMessage','/api/chat.update'].includes(req.url))return reply(501,{ok:false,error:'unsupported_fixture_operation'});
  const body=req.body;
  if(req.headers.authorization!=='Bearer synthetic-slack-token'||body?.token)return reply(401,{ok:false,error:'invalid_auth'});
  calls.push({method:req.url,channel:body?.channel,text:body?.text,ts:body?.ts,thread_ts:body?.thread_ts,reply_broadcast:body?.reply_broadcast,blocks:body?.blocks});
  if(body?.thread_ts!==undefined&&(req.url!=='/api/chat.postMessage'||!/^\d+\.\d+$/.test(body.thread_ts)||body.reply_broadcast!==false))return reply(200,{ok:false,error:'invalid_thread'});
  if(body?.channel!=='CSELECTED'||typeof body?.text!=='string')return reply(200,{ok:false,error:'channel_not_found'});
  if(body.text==='fixture:rate-limit')return reply(429,{ok:false,error:'ratelimited'},{'Retry-After':'1'});
  if(body.text==='fixture:reject')return reply(200,{ok:false,error:'PRIVATE_PROVIDER_DETAIL'});
  if(body.text==='fixture:server-error')return reply(500,{ok:false,error:'internal_error'});
  if(req.url==='/api/chat.update'&&!/^\d+\.\d+$/.test(body.ts||''))return reply(200,{ok:false,error:'invalid_ts'});
  if(body.blocks?.some(block=>block.type==='section'&&(typeof block.text?.text!=='string'||[...block.text.text].length>3000)))return reply(200,{ok:false,error:'invalid_blocks'});
  return reply(200,{ok:true,channel:'CSELECTED',ts:body.ts||'1700000000.000001',message:{text:body.text}});
}).listen(process.env.PORT||49874);
