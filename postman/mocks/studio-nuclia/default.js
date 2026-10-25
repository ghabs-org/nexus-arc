// Shapes: Nuclia's official SyncAskResponse and KnowledgeboxFindResults models.
// No provider account, embedding service or model is contacted.
const http = require('http');
const paragraph = 'resource/f/document/0-12';
const retrieval = {resources: {resource: {fields: {'f/document': {paragraphs: {
  [paragraph]: {id: paragraph, text: 'Thirty days.', position: {page_number: 4}},
  uncited: {id: 'uncited', text: 'This paragraph was retrieved but not cited.'}
}}}}}, best_matches: [paragraph], next_page: false, total: 2};
let attempts = 0;
http.createServer((req, res) => {
  const reply = (status, body) => {res.writeHead(status, {'Content-Type':'application/json'});res.end(JSON.stringify(body));};
  if (req.method === 'GET' && req.url === '/fixture/attempts') return reply(200, {attempts});
  const url=new URL(req.url,'http://localhost');
  const conversation=url.pathname.match(/^\/api\/v1\/kb\/selected\/resource\/(?:resource|r)\/conversation\/(discussion|reject|redirect|partial|oversized|malformed)$/);
  const attachment=url.pathname.match(/^\/api\/v1\/kb\/selected\/resource\/(?:resource|r)\/conversation\/discussion\/download\/field\/(message%3Aone|message-two)\/0$/);
  if(req.method==='GET'&&(conversation||attachment)){
    attempts++;
    if(req.headers['x-nuclia-serviceaccount']!=='Bearer synthetic-nuclia-key'||req.headers.authorization)return reply(401,{});
    if(attachment){res.writeHead(200,{'Content-Type':'text/plain'});return res.end('Original conversation attachment: '+decodeURIComponent(attachment[1]));}
    const field=conversation[1],page=Number(url.searchParams.get('page'));
    if(url.searchParams.get('show')!=='value'||![1,2].includes(page))return reply(422,{});
    if(field==='reject')return reply(403,{error:'PRIVATE_PROVIDER_DETAIL'});
    if(field==='redirect'){res.writeHead(307,{Location:'https://example.invalid/never-fetch'});return res.end();}
    if(field==='partial'){res.writeHead(206,{'Content-Type':'application/json'});return res.end('{}');}
    if(field==='oversized')return reply(200,{text:'x'.repeat(300000)});
    if(field==='malformed')return reply(200,{field_type:'conversation',field_id:'wrong',value:{}});
    return reply(200,{field_id:field,field_type:'conversation',value:{page,pages:2,total:2,messages:[{ident:page===1?'message:one':'message-two',content:{text:'UNRELATED_PRIVATE_MESSAGE',attachments:[{filename:'policy.txt',uri:'https://untrusted.invalid/?token=EPHEMERAL_SECRET'}],attachments_fields:page===1?[{field_type:'file',field_id:'supporting'},{field_type:'text',field_id:'notes'}]:[]}}]}});
  }
  const original=req.url.match(/^\/api\/v1\/kb\/selected\/resource\/(?:resource|r)\/file\/(document|file|supporting|reject|redirect|partial|oversized)\/download\/field$/);
  if(req.method==='GET'&&original){
    attempts++;
    if(req.headers['x-nuclia-serviceaccount']!=='Bearer synthetic-nuclia-key'||req.headers.authorization)return reply(401,{});
    if(original[1]==='reject')return reply(403,{error:'PRIVATE_PROVIDER_DETAIL'});
    if(original[1]==='redirect'){res.writeHead(307,{Location:'https://example.invalid/never-fetch'});return res.end();}
    if(original[1]==='partial'){res.writeHead(206,{'Content-Type':'text/plain'});return res.end('partial');}
    res.writeHead(200,{'Content-Type':'text/plain; charset=utf-8'});
    return res.end(original[1]==='oversized'?'x'.repeat(2048):'Original policy file: Thirty days.');
  }

  if (req.method !== 'POST' || !['/api/v1/kb/selected/find','/api/v1/kb/selected/ask'].includes(req.url)) return reply(501, {});
  attempts++;
  if (req.headers['x-nuclia-serviceaccount'] !== 'Bearer synthetic-nuclia-key' || req.headers.authorization) return reply(401, {error:'PRIVATE_ACCOUNT_DETAIL'});
  const body=req.body;
  if (!body?.query || body.top_k !== 10) return reply(400, {});
  if(body.audit_metadata){
    if(typeof body.audit_metadata!=='object'||Array.isArray(body.audit_metadata)||Object.values(body.audit_metadata).some(value=>typeof value!=='string')||Buffer.byteLength(JSON.stringify(body.audit_metadata))>10240)return reply(422,{});
  }
  if(body.query==='fixture:audit'||body.query==='fixture:audit-reject'){
    if(!body.audit_metadata||Object.keys(body.audit_metadata).length!==3||body.audit_metadata.scope!=='workspace'||body.audit_metadata.question!==body.query||body.audit_metadata.environment!=='local-fixture')return reply(422,{});
    if(body.query==='fixture:audit-reject')return reply(429,{error:'PRIVATE_AUDIT_PROVIDER_DETAIL'});
  }
  if (body.filter_expression && body.filter_expression.field?.id !== 'resource') return reply(400, {});
  if (body.query.startsWith('Region:') && body.query !== 'Region: emea; Question: What is the return policy?') return reply(400, {});
  if (body.query.startsWith('Workspace:') && body.query !== 'Workspace: workspace; Question: What is the return policy?') return reply(400, {});
  if (req.url.endsWith('/find')) return reply(200, {...retrieval, page_number:body.page_number||0});
  if (req.headers['x-synchronous'] !== 'true' || !['default','none','llm_footnotes'].includes(body.citations)) return reply(400, {});
  if(body.query.startsWith('fixture:presentation')){
    if(body.query==='fixture:presentation-reject')return reply(429,{error:'PRIVATE_PROVIDER_DETAIL'});
    if(body.citations==='none')return reply(200,{status:'success',answer:'Thirty days.',retrieval_results:retrieval,citations:{}});
    if(body.citations==='llm_footnotes')return reply(200,{status:'success',answer:'Thirty days.[^policy]',retrieval_results:retrieval,citations:{},citation_footnote_to_context:{policy:paragraph}});
    return reply(200,{status:'success',answer:'Thirty days.',retrieval_results:retrieval,citations:{[paragraph]:[[0,12]]}});
  }
  if(body.query==='fixture:expanded'){
    const strategies=body.rag_strategies;
    if(!Array.isArray(strategies)||strategies.length!==2||strategies[0].name!=='neighbouring_paragraphs'||strategies[0].before!==2||strategies[0].after!==2||strategies[1].name!=='hierarchy'||strategies[1].count!==0)return reply(422,{});
    const neighbour='resource/f/document/12-24',title='resource/a/title';
    return reply(200,{status:'success',answer:'Thirty days.',retrieval_results:{resources:{}},citations:{[neighbour]:[[0,12]],[title]:[[0,12]]},augmented_context:{paragraphs:{[neighbour]:{id:neighbour,text:'Return unused items within thirty days.',position:{page_number:6},augmentation_type:'neighbouring_paragraphs'}},fields:{[title]:{id:title,text:'Returns policy',augmentation_type:'hierarchy'}}}});
  }
  if(body.query==='fixture:conversation'){
    const id='resource/c/discussion';
    return reply(200,{status:'success',answer:'Conversation evidence.',retrieval_results:{resources:{}},citations:{[id]:[[0,12]]},augmented_context:{fields:{[id]:{id,text:'Cited conversation evidence.',augmentation_type:'conversation'}}}});
  }
  if (body.query === 'fixture:augmented') {
    const id = 'resource/f/document/12-24';
    return reply(200, {status: 'success', answer: 'Neighbouring evidence.',
      retrieval_results: {resources: {}}, citations: {[id]: [[0, 21]]},
      augmented_context: {paragraphs: {[id]: {id, text: 'Neighbouring evidence.',
        position: {page_number: 6}, augmentation_type: 'neighbouring_paragraphs'}}}});
  }
  if (body.query === 'fixture:reject') return reply(429, {error:'PRIVATE_PROVIDER_DETAIL'});
  if (body.query === 'fixture:redirect') {res.writeHead(307, {Location:'https://example.invalid/never-fetch'});return res.end();}
  if (body.query === 'fixture:bad-json') {res.writeHead(200, {'Content-Type':'application/json'});return res.end('not-json');}
  if (body.query === 'fixture:oversized') return reply(200, {answer:'x'.repeat(300000)});
  if (body.query === 'fixture:error') return reply(200, {status:'error',answer:'PRIVATE_PROVIDER_DETAIL',retrieval_results:retrieval,citations:{}});
  if (body.query === 'fixture:missing-citation') return reply(200, {status:'success',answer:'Unsafe answer',retrieval_results:retrieval,citations:{missing:[[0,3]]}});
  if (body.query === 'fixture:empty') return reply(200, {status:'no_context',answer:'No evidence found.',retrieval_results:{resources:{}},citations:{}});
  if (body.prompt && !body.prompt.user.includes('{question}')) return reply(400, {});
  return reply(200, {status:'success',answer:'Thirty days.',retrieval_results:retrieval,citations:{[paragraph]:[[0,12]]},learning_id:'synthetic-learning-id'});
}).listen(process.env.PORT||49873);
