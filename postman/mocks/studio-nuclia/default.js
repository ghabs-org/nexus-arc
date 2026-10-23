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
  const original=req.url.match(/^\/api\/v1\/kb\/selected\/resource\/(?:resource|r)\/file\/(document|file|reject|redirect|partial|oversized)\/download\/field$/);
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
  if (body.filter_expression && body.filter_expression.field?.id !== 'resource') return reply(400, {});
  if (body.query.startsWith('Region:') && body.query !== 'Region: emea; Question: What is the return policy?') return reply(400, {});
  if (body.query.startsWith('Workspace:') && body.query !== 'Workspace: workspace; Question: What is the return policy?') return reply(400, {});
  if (req.url.endsWith('/find')) return reply(200, {...retrieval, page_number:body.page_number||0});
  if (req.headers['x-synchronous'] !== 'true' || body.citations !== 'default') return reply(400, {});
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
