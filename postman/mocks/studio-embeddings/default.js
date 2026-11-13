// Synthetic compatibility service derived from postman/contracts/studio-embeddings.yaml.
// No vendor-quality or live-account claim. Only these client paths are modeled.
const http = require('http');
http.createServer((req, res) => {
  const reply=(status,body,type='application/json')=>{res.writeHead(status,{'Content-Type':type});res.end(type==='application/json'?JSON.stringify(body):body);};
  if(req.method!=='POST'||req.url!=='/v1/embeddings')return reply(501,{error:'Unmodeled compatibility operation'});
  const data=req.body;
  if(!data||typeof data.model!=='string'||!Array.isArray(data.input)||!data.input.length||data.input.length>257||data.input.some(text=>typeof text!=='string'))return reply(400,{error:'Invalid model/input'});
  if(data.model==='unavailable')return reply(503,{error:'Synthetic service unavailable'});
  if(data.model==='unauthorized')return reply(401,{error:'Synthetic credentials rejected'});
  if(data.model==='html-error')return reply(500,'<html>Unavailable</html>','text/html');
  if(data.model==='malformed')return reply(200,{data:[{index:0,embedding:['not-a-number']}]});
  const embedding=text=>{text=text.toLowerCase();return [text.includes('refund')||text.includes('return')?1:0,text.includes('invoice')||text.includes('payment')?1:0,0.01];};
  reply(200,{data:data.input.map((text,index)=>({index,embedding:embedding(text)})).reverse()});
}).listen(process.env.PORT||49641);
