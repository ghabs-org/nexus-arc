// Local contract fixture derived from the official Decisions API shapes.
// No real model, account, external state or automatic side effects.
const http = require('http');
const attempts = new Map();
http.createServer((req,res)=>{
  const reply=(status,body,type='application/json')=>{res.writeHead(status,{'Content-Type':type});res.end(type==='application/json'?JSON.stringify(body):body);};
  if(req.method!=='POST'||req.url!=='/api/alpha/decisions')return reply(501,{error:'Unmodeled compatibility operation'});
  if(req.headers.authorization!=='Bearer synthetic-fixture-key')return reply(401,{error:{type:'authentication_error'}});
  const data=req.body;
  if(!data||typeof data.model!=='string'||!data.questions||typeof data.questions!=='object')return reply(400,{error:{type:'invalid_request'}});
  const mode=data.model;
  const error=(status,type)=>reply(status,{error:{type,message:'DO_NOT_LOG_PROVIDER_DETAIL'}});
  if(mode==='unauthorized')return error(401,'authentication_error');
  if(mode==='forbidden-large')return reply(403,'DO_NOT_LOG_PROVIDER_DETAIL'.repeat(100000),'text/plain');
  if(mode==='quota')return error(429,'insufficient_quota');
  if(mode==='bad-request')return error(422,'invalid_request');
  if(mode==='too-large')return error(413,'input_too_large');
  if(mode==='rate-limited')return error(429,'rate_limit_exceeded');
  if(mode==='unavailable')return error(503,'unavailable');
  if(mode==='html-error')return reply(500,'<html>DO_NOT_LOG_PROVIDER_DETAIL</html>','text/html');
  if(mode==='malformed')return reply(200,{answers:{}});
  if(mode==='oversized')return reply(200,' '.repeat(1100000),'application/json');
  if(mode==='redirect'){res.writeHead(302,{Location:'http://127.0.0.1:1/not-followed'});return res.end();}
  if(mode==='recover-once'){
    const n=attempts.get(mode)||0;attempts.set(mode,n+1);
    if(n===0)return error(503,'unavailable');
  }
  const answers={};
  for(const [name,q] of Object.entries(data.questions)){
    if(q.type==='noul')answers[name]={type:'noul',noul:.8};
    else if(q.type==='choice'){
      const keys=Object.keys(q.criteria||{}),choice=keys.includes('billing')?'billing':keys[0];
      answers[name]={type:'choice',choice,probabilities:Object.fromEntries(keys.map(k=>[k,k===choice?1:0])),confidence:mode==='low-confidence'?.2:.9};
    }else if(q.type==='score'&&Array.isArray(q.criteria)){
      answers[name]={type:'score',score:q.criteria.length-1,confidence:.9,probabilities:Object.fromEntries(q.criteria.map((_,i)=>[String(i),i===q.criteria.length-1?1:0])),legend:Object.fromEntries(q.criteria.map((label,i)=>[String(i),label]))};
    }else return error(400,'invalid_question');
  }
  if(mode==='refused'){const name=Object.keys(answers)[0];answers[name]={type:'refusal',refusal:'DO_NOT_LOG_PROVIDER_DETAIL'};}
  const body={model:mode+'-resolved',answers,usage:{input_tokens:42,output_tokens:0}};
  if(mode==='slow')return setTimeout(()=>reply(200,body),2000);
  reply(200,body);
}).listen(process.env.PORT||49642);
