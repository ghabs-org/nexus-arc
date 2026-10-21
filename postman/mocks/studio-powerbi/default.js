// Official Execute Queries shapes, with synthetic DAX markers selecting fixture errors.
// https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/execute-queries
const http = require('http');
const dataset = 'cfafbeb1-8037-4d0c-896e-a46fb27ff229';
const workspace = '11111111-2222-3333-4444-555555555555';
let attempts = 0;
http.createServer((req, res) => {
  const reply = (status, body) => {
    res.writeHead(status, {'Content-Type': 'application/json'});
    res.end(JSON.stringify(body));
  };
  if (req.method === 'GET' && req.url === '/fixture/attempts') return reply(200, {attempts});
  const paths = ['/v1.0/myorg/datasets/' + dataset,
    '/v1.0/myorg/groups/' + workspace + '/datasets/' + dataset];
  const query = paths.some(path => req.url === path + '/executeQueries');
  const metadata = paths.includes(req.url);
  if (!(query && req.method === 'POST' || metadata && req.method === 'GET')) return reply(501, {});
  attempts++;
  if (req.headers.authorization !== 'Bearer synthetic-powerbi-key') return reply(401, {error: {message: 'private credential detail'}});
  if (metadata) return reply(200, {id: dataset, name: 'Synthetic revenue model', isRefreshable: true});
  const body = req.body;
  if (body?.queries?.length !== 1 || typeof body.queries[0].query !== 'string' ||
      body.serializerSettings?.includeNulls !== true || 'impersonatedUserName' in body) return reply(400, {});
  const dax = body.queries[0].query;
  const error = {code: 'SyntheticQueryError', message: 'PRIVATE_PROVIDER_DETAIL'};
  const table = {rows: [{'[Revenue]': 42, '[Missing]': null}]};
  if (dax.includes('fixture:reject')) return reply(429, {error});
  if (dax.includes('fixture:redirect')) {
    res.writeHead(307, {Location: 'https://example.invalid/never-query'}); return res.end();
  }
  if (dax.includes('fixture:bad-json')) {res.writeHead(200, {'Content-Type': 'application/json'}); return res.end('not-json');}
  if (dax.includes('fixture:oversized')) return reply(200, {results: [{tables: [{rows: [{secret: 'x'.repeat(300000)}]}]}]});
  if (dax.includes('fixture:top-error')) return reply(200, {error, results: [{tables: [table]}]});
  if (dax.includes('fixture:result-error')) return reply(200, {results: [{error, tables: [table]}]});
  if (dax.includes('fixture:table-error')) return reply(200, {results: [{tables: [{...table, error}]}]});
  if (dax.includes('fixture:two-tables')) return reply(200, {results: [{tables: [table, table]}]});
  if (dax.includes('fixture:malformed')) return reply(200, {results: [{tables: [{rows: [42]}]}]});
  if (dax.includes('fixture:empty')) return reply(200, {results: [{tables: [{rows: []}]}]});
  return reply(200, {results: [{tables: [table]}], informationProtectionLabel: {id: 'fixture-label', name: 'Private'}});
}).listen(process.env.PORT || 49872);
