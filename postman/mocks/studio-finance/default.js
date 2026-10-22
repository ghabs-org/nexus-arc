// yfinance 0.2.65 quoteSummary, quote and complementary time-series response shapes.
// Deliberately synthetic data, no live market information or provider credentials.
const http=require('http');let requests=0;
http.createServer((req,res)=>{
  const reply=(status,body)=>{res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(body));};
  const url=new URL(req.url,'http://fixture.invalid');
  if(url.pathname==='/fixture/attempts')return reply(200,{requests});
  requests++;
  if(req.method!=='GET'||!/^\/(v10\/finance\/quoteSummary\/|v7\/finance\/quote|ws\/fundamentals-timeseries\/)/.test(url.pathname))return reply(501,{});
  if(req.headers.authorization||req.headers.cookie)return reply(403,{error:'Credentials must not reach this fixture'});
  const symbol=url.searchParams.get('symbols')||url.searchParams.get('symbol')||url.pathname.split('/').pop();
  if(symbol==='FAIL')return reply(429,{error:'PRIVATE_PROVIDER_DETAIL'});
  if(symbol==='BADJSON'){res.writeHead(200,{'Content-Type':'application/json'});return res.end('not-json');}
  const info={symbol,regularMarketPrice:125.5,previousClose:120,dayHigh:127,dayLow:119,
    volume:1000,fiftyTwoWeekHigh:150,fiftyTwoWeekLow:75,currency:'USD',financialCurrency:'USD',
    regularMarketTime:1700000000,shortName:'Example Company',sector:'Technology',industry:'Software',
    marketCap:1000000,trailingPE:20,forwardPE:18,trailingEps:6,dividendYield:0,totalRevenue:10000,
    profitMargins:0.25,operatingMargins:0.3,debtToEquity:12,returnOnEquity:0.2,bookValue:24,
    exDividendDate:1700000000,longBusinessSummary:'PRIVATE_UNSELECTED_FIELD'};
  if(symbol==='ZERO')Object.assign(info,{regularMarketPrice:0,currentPrice:99,previousClose:0,volume:0});
  if(symbol==='MISSING'){delete info.regularMarketPrice;delete info.shortName;}
  if(url.pathname.includes('/quoteSummary/'))return reply(200,{quoteSummary:{result:[{symbol,financialData:info}],error:null}});
  if(url.pathname.startsWith('/v7/finance/quote'))return reply(200,{quoteResponse:{result:[info],error:null}});
  return reply(200,{timeseries:{result:[{}],error:null}});
}).listen(process.env.PORT||49874);
