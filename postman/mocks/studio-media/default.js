// Synthetic media only. Shapes follow the inspected official API contracts.
const http=require('http');
const png='iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFUlEQVR4nGNkaPjfwMDAwMAEIkAYAB4nAgOIIIfEAAAAAElFTkSuQmCC';
http.createServer((req,res)=>{
 const reply=(status,value)=>{if(res.destroyed)return;res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(value));};
 if(req.method!=='POST')return reply(501,{});
 if(req.headers.authorization!=='Bearer synthetic-media-key')return reply(401,{error:{message:'private credential failure'}});
 const body=req.body;
 const prompt=body?.prompt||body?.messages?.[0]?.content?.[0]?.text;
 if(prompt==='fixture:failure')return reply(429,{error:{message:'SECRET_PROVIDER_DETAIL'}});
 if(prompt==='fixture:redirect'){res.writeHead(302,{Location:'https://example.invalid/never-fetch'});return res.end();}
 if(prompt==='fixture:timeout')return setTimeout(()=>reply(200,{data:[{b64_json:png}]}),1800);
 if(prompt==='fixture:bad-json'){res.writeHead(200,{'Content-Type':'application/json'});return res.end('not-json');}
 if(!body?.model||!prompt)return reply(400,{});
 if(req.url==='/v1/images/generations'||req.url==='/v1/images/edits'){
  if(body.n!==1||body.output_format!=='png')return reply(400,{});
  if(req.url.endsWith('/edits')){
   if(body.images?.length!==1||!body.images[0].image_url.startsWith('data:image/png;base64,'))return reply(400,{});
   if(body.mask&&!body.mask.image_url.startsWith('data:image/png;base64,'))return reply(400,{});
  }
  if(prompt==='fixture:url-output')return reply(200,{data:[{url:'https://example.invalid/never-fetch'}]});
  return reply(200,{data:[{b64_json:png}],usage:{input_tokens:2,output_tokens:3,total_tokens:5}});
 }
 if(req.url==='/v1/chat/completions'){
  const part=body.messages?.[0]?.content?.[1];
  if(body.store!==false||body.max_completion_tokens!==2048)return reply(400,{});
  if(part?.type==='image_url'){
   if(!part.image_url.url.startsWith('data:image/png;base64,'))return reply(400,{});
  }else if(part?.type==='input_audio'){
   if(part.input_audio.format!=='wav'||JSON.stringify(body.modalities)!=='["text"]')return reply(400,{});
  }else return reply(400,{});
  if(prompt==='fixture:refusal')return reply(200,{choices:[{finish_reason:'stop',message:{content:null,refusal:'not allowed'}}]});
  if(prompt==='fixture:truncated')return reply(200,{choices:[{finish_reason:'length',message:{content:'partial'}}]});
  if(prompt==='fixture:oversized')return reply(200,{choices:[{finish_reason:'stop',message:{content:'x'.repeat(32769)}}]});
  return reply(200,{choices:[{finish_reason:'stop',message:{content:part.type==='input_audio'?'A short synthetic recording.':'A synthetic blue image.'}}],usage:{prompt_tokens:2,completion_tokens:3,total_tokens:5}});
 }
 return reply(501,{});
}).listen(process.env.PORT||49831);
