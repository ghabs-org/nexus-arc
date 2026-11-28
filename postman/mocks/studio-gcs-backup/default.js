// JSON Objects.get metadata/media contract; synthetic data and no cloud credentials.
const http = require('http');
const calls = [];
http.createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost');
  const json = (status, body) => {
    res.writeHead(status, {'Content-Type': 'application/json'});
    res.end(JSON.stringify(body));
  };
  if (req.method === 'GET' && url.pathname === '/_fixture/calls') return json(200, calls);
  const match = url.pathname.match(/^\/(download\/)?storage\/v1\/b\/evidence\/o\/(.+)$/);
  calls.push({method: req.method, path: url.pathname,
    range: req.headers.range || null, generation: url.searchParams.get('ifGenerationMatch'),
    authenticated: Boolean(req.headers.authorization)});
  const error = (status, message) => json(status, {error: {code: status, message}});
  // Current SDKs also fetch bucket metadata for their observability cache.
  if (req.method === 'GET' && url.pathname === '/storage/v1/b/evidence') {
    return json(200, {kind: 'storage#bucket', name: 'evidence', location: 'US-CENTRAL1',
      locationType: 'region', projectNumber: '123456789'});
  }
  if (req.method !== 'GET' || !match) return error(501, 'Not implemented by fixture');
  const key = decodeURIComponent(match[2]);
  if (key === 'missing') return error(404, 'Synthetic missing object');
  if (key === 'denied') return error(403, 'Synthetic denied object');
  if (key === 'outage') return error(503, 'Synthetic unavailable service');
  let content;
  if (['corrupt', 'changed', 'overrun', 'oversize'].includes(key)) content = Buffer.from('X'.repeat(key === 'oversize' ? 64 : 14));
  else if (key === 'empty') content = Buffer.alloc(0);
  else if (key.endsWith('/1')) content = Buffer.from('Original policy refunds');
  else if (key.endsWith('/2')) content = Buffer.from('Updated policy');
  else return error(501, 'Unknown fixture object');
  if (!match[1]) return json(200, {kind: 'storage#object', bucket: 'evidence',
    name: key, size: String(content.length), generation: '7', metageneration: '1'});
  if (url.searchParams.get('ifGenerationMatch') !== '7') return error(400, 'Generation precondition required');
  if (key === 'changed') return error(412, 'Synthetic generation changed');
  const range = req.headers.range?.match(/^bytes=0-(\d+)$/);
  if (!range) return error(400, 'Bounded range required');
  // The overrun fixture deliberately violates the server contract to test the final byte guard.
  if (key === 'overrun') content = Buffer.from('X'.repeat(Number(range[1]) + 2));
  const body = key === 'overrun' ? content : content.subarray(0, Number(range[1]) + 1);
  res.writeHead(key === 'overrun' ? 200 : 206, {'Content-Type': 'application/octet-stream',
    'Content-Length': body.length,
    ...(key === 'overrun' ? {} : {'Content-Range': `bytes 0-${body.length - 1}/${content.length}`})});
  res.end(body);
}).listen(process.env.PORT);
