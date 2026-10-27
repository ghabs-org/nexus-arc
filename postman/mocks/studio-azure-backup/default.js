// Azure HEAD/Get Blob contract. Never validates real signatures or handles writes.
const http = require('http');
const calls = [];
http.createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (req.method === 'GET' && url.pathname === '/_fixture/calls') {
    res.writeHead(200, {'Content-Type': 'application/json'});
    return res.end(JSON.stringify(calls));
  }
  calls.push({method: req.method, path: url.pathname,
    range: req.headers['x-ms-range'] || req.headers.range || null,
    authenticated: req.headers.authorization?.startsWith('SharedKey fixtureaccount:') || false});
  const error = (status, code) => {
    res.writeHead(status, {'Content-Type': 'application/xml', 'x-ms-error-code': code});
    res.end(`<Error><Code>${code}</Code><Message>Synthetic fixture</Message></Error>`);
  };
  if (!['GET', 'HEAD'].includes(req.method) || !url.pathname.startsWith('/fixtureaccount/evidence/')) return error(501, 'NotImplemented');
  if (!calls[calls.length - 1].authenticated) return error(403, 'AuthenticationFailed');
  const key = decodeURIComponent(url.pathname.slice('/fixtureaccount/evidence/'.length));
  if (key === 'missing') return error(404, 'BlobNotFound');
  if (key === 'denied') return error(403, 'AuthorizationPermissionMismatch');
  let content;
  if (['corrupt', 'overrun', 'oversize'].includes(key)) content = Buffer.from('X'.repeat(key === 'oversize' ? 64 : 14));
  else if (key === 'empty') content = Buffer.alloc(0);
  else if (key.endsWith('/1')) content = Buffer.from('Original policy refunds');
  else if (key.endsWith('/2')) content = Buffer.from('Updated policy');
  else return error(501, 'NotImplemented');
  const headers = {'Content-Type': 'application/octet-stream', ETag: '"fixture-etag"',
    'Last-Modified': 'Mon, 26 Oct 2026 09:00:00 GMT', 'x-ms-blob-type': 'BlockBlob',
    'x-ms-request-id': 'synthetic-request', 'x-ms-version': '2025-11-05'};
  if (req.method === 'HEAD') {
    res.writeHead(200, {...headers, 'Content-Length': content.length});
    return res.end();
  }
  const range = (req.headers['x-ms-range'] || req.headers.range || '').match(/^bytes=0-(\d+)$/);
  if (!range) return error(400, 'InvalidRange');
  if (key === 'overrun') content = Buffer.from('X'.repeat(Number(range[1]) + 2));
  const body = key === 'overrun' ? content : content.subarray(0, Number(range[1]) + 1);
  res.writeHead(206, {...headers, 'Content-Length': body.length,
    'Content-Range': `bytes 0-${body.length - 1}/${content.length}`});
  res.end(body);
}).listen(process.env.PORT);
