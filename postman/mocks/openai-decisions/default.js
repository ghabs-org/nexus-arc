// Official Decisions guide shapes; synthetic local data only.
// Owner mock search unavailable without Postman login; Orbit returned HTTP 403.
const http=require('http');
const attempts=new Map();
http.createServer((req,res)=>{
 const reply=(status,body,type='application/json')=>{res.writeHead(status,{'Content-Type':type});res.end(type==='application/json'?JSON.stringify(body):body);};
 if(req.method!=='POST'||req.url!=='/v1/decisions')return reply(501,{error:'Unmodeled dependency operation'});
 if(req.headers.authorization!=='Bearer synthetic-fixture-key')return reply(401,{error:{type:'authentication_error'}});
 const data=req.body;
 if(!data||data.model!=='gpt-6-luna'||!Array.isArray(data.questions)||typeof data.input!=='string')return reply(400,{error:{type:'invalid_request'}});
 let state;try{state=JSON.parse(data.input);}catch{state={ticket:data.input};}
 const mode=state.ticket;
 const error=(status,type)=>reply(status,{error:{type,message:'DO_NOT_LOG_NATIVE_DETAIL'}});
 if(mode==='unauthorized')return error(401,'authentication_error');
 if(mode==='quota')return error(429,'insufficient_quota');
 if(mode==='rate-limited')return error(429,'rate_limit_exceeded');
 if(mode==='unavailable')return error(503,'unavailable');
 if(mode==='html-error')return reply(500,'<html>DO_NOT_LOG_NATIVE_DETAIL</html>','text/html');
 if(mode==='oversized')return reply(200,' '.repeat(1100000),'application/json');
 if(mode==='redirect'){res.writeHead(302,{Location:'http://127.0.0.1:1/not-followed'});return res.end();}
 if(mode==='recover-once'){
   const key='native:'+JSON.stringify(data.questions),n=attempts.get(key)||0;attempts.set(key,n+1);
   if(n===0)return error(503,'unavailable');
 }
 const answers=[];
 for(const question of data.questions){
   if(!question.name||typeof question.instructions!=='string')return error(400,'invalid_question');
   const {name,type}=question;
   if(type==='predicate')answers.push({name,type,probability:.8});
   else if(type==='choice'&&Array.isArray(question.options)){
     const choice=question.options.find(q=>q.value==='billing')?.value||question.options[0]?.value;
     answers.push({name,type,choice,confidence:mode==='low-confidence'?.2:.9,probabilities:question.options.map(q=>({value:q.value,probability:q.value===choice?1:0}))});
   }else if(type==='score'&&Array.isArray(question.levels)){
     const last=question.levels.length-1;
     answers.push({name,type,score:last,confidence:.9,probabilities:question.levels.map((q,i)=>({value:i,label:q.label,probability:i===last?1:0}))});
   }else return error(400,'invalid_question');
 }
 if(mode==='refused')answers[0]={name:answers[0].name,type:'refusal',reason:'DO_NOT_LOG_NATIVE_DETAIL'};
 if(mode==='duplicate')answers[answers.length-1]=answers[0];
 if(mode==='malformed')answers.pop();
 if(mode==='wrong-score-label')answers.find(q=>q.type==='score').probabilities[0].label='invented';
 const body={model:'gpt-6-luna',answers,usage:{input_tokens:42,output_tokens:0}};
 if(mode==='slow')return setTimeout(()=>reply(200,body),2000);
 reply(200,body);
}).listen(process.env.PORT||49643);
