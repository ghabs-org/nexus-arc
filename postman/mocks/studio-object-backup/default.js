// S3 GetObject contract only. All object content and signing credentials are synthetic.
const http = require('http');
http.createServer((req, res) => {
  const path = new URL(req.url, 'http://localhost').pathname;
  const error = (status, code) => {
    res.writeHead(status, {'Content-Type': 'application/xml'});
    res.end(`<Error><Code>${code}</Code><Message>Synthetic backup fixture</Message></Error>`);
  };
  if (req.method !== 'GET' || !path.startsWith('/evidence/')) return error(501, 'NotImplemented');
  if (!req.headers.authorization?.includes('Credential=fixture-access/')) return error(403, 'AccessDenied');
  const key = path.slice('/evidence/'.length);
  if (key === 'missing') return error(404, 'NoSuchKey');
  if (key === 'denied') return error(403, 'AccessDenied');
  if (key === 'outage') return error(503, 'ServiceUnavailable');
  let content;
  if (key === 'corrupt') content = 'X'.repeat(14);
  else if (key === 'oversize') content = 'X'.repeat(64);
  else if (key === 'empty') content = '';
  else if (key.endsWith('/1')) content = 'Original policy refunds';
  else if (key.endsWith('/2')) content = 'Updated policy';
  else return error(501, 'NotImplemented');
  res.writeHead(200, {'Content-Type': 'text/plain', 'Content-Length': Buffer.byteLength(content)});
  res.end(content);
}).listen(process.env.PORT || 49941);
