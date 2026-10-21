// Synthetic responses follow documented Open-Meteo fields; not real weather.
const http=require('http');
http.createServer((req,res)=>{
 const url=new URL(req.url,'http://localhost');
 const reply=(status,value)=>{res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(value));};
 if(req.method!=='GET'||req.headers.authorization||url.searchParams.has('apikey'))return reply(400,{error:true});
 if(url.pathname==='/v1/search'){
  const city=url.searchParams.get('name');
  if(city==='Failure')return reply(503,{error:true,reason:'synthetic outage'});
  if(city==='Redirect'){res.writeHead(302,{Location:'https://example.invalid/never-follow'});return res.end();}
  if(city==='Unknown')return reply(200,{});
  if(city==='BadSchema')return reply(200,{results:[{name:'Bad',latitude:999}]});
  if(!city||!['json'].includes(url.searchParams.get('format'))||Number(url.searchParams.get('count'))>5)return reply(400,{error:true});
  return reply(200,{results:[{name:city,latitude:city==='WeatherFailure'?0:41.9,longitude:12.5,country_code:'IT',country:'Italy',timezone:'Europe/Rome'}]});
 }
 if(url.pathname==='/v1/forecast'){
  if(url.searchParams.get('latitude')==='0')return reply(503,{error:true});
  const days=Number(url.searchParams.get('forecast_days'));
  if(!Number.isInteger(days)||days<1||days>7||url.searchParams.get('timezone')!=='Europe/Rome'||!url.searchParams.get('current')?.includes('temperature_2m'))return reply(400,{error:true});
  return reply(200,{
   current:{time:'2026-10-21T12:00',temperature_2m:22,relative_humidity_2m:60,weather_code:1,wind_speed_10m:5},
   current_units:{time:'iso8601',temperature_2m:'°C',relative_humidity_2m:'%',weather_code:'wmo code',wind_speed_10m:'km/h'},
   daily:{time:Array.from({length:days},(_,i)=>'2026-10-'+(21+i)),weather_code:Array(days).fill(1),temperature_2m_max:Array(days).fill(24),temperature_2m_min:Array(days).fill(12),precipitation_sum:Array(days).fill(0)},
   daily_units:{time:'iso8601',weather_code:'wmo code',temperature_2m_max:'°C',temperature_2m_min:'°C',precipitation_sum:'mm'}
  });
 }
 return reply(501,{error:true,reason:'Not part of geographic contract'});
}).listen(process.env.PORT||49821);
