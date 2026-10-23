// Synthetic responses for the existing OpenAI SDK speech/transcription calls.
const http=require('http');
http.createServer((req,res)=>{
 const reply=(status,value)=>{res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(value));};
 if(req.method!=='POST')return reply(501,{error:{message:'Fixture only implements speech POSTs'}});
 if(req.headers.authorization!=='Bearer synthetic-speech-key')return reply(401,{error:{message:'Synthetic credential rejected'}});
 if(req.url==='/v1/audio/speech'){
  const body=req.body;
  if(body?.input==='fixture:failure')return reply(429,{error:{message:'PRIVATE_PROVIDER_DETAIL'}});
  if(body?.model!=='tts-1'||body?.voice!=='nova'||body?.response_format!=='mp3')return reply(400,{error:{message:'Pinned speech settings changed'}});
  res.writeHead(200,{'Content-Type':'audio/mpeg'});return res.end(Buffer.from('ID3synthetic-speech'));
 }
 if(req.url==='/v1/audio/transcriptions'){
  if(!req.headers['content-type']?.startsWith('multipart/form-data'))return reply(400,{error:{message:'Expected multipart audio'}});
  return reply(200,{text:'Pinned transcription from synthetic audio'});
 }
 return reply(501,{error:{message:'Endpoint outside fixture contract'}});
}).listen(process.env.PORT||49832);
