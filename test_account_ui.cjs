const assert=require('node:assert/strict');
const fs=require('node:fs');
const {JSDOM,VirtualConsole}=require(process.env.CHYPER_JSDOM_PATH||'jsdom');
const html=fs.readFileSync('templates/index.html','utf8');
const source=fs.readFileSync('static/account.js','utf8');
const wait=ms=>new Promise(r=>setTimeout(r,ms));
async function boot({active=true,expires=Math.floor(Date.now()/1000)+86400,draft=null,saveDelay=0}={}){
 const problems=[],requests=[];
 const vc=new VirtualConsole();vc.on('jsdomError',e=>problems.push(e.message));
 let revision=0,stored=draft;
 const dom=new JSDOM(html,{url:'https://chyper.test/',runScripts:'dangerously',virtualConsole:vc,beforeParse(w){
  w.scrollTo=()=>{};w.Headers=Headers;w.Response=Response;
  w.fetch=async(url,opts={})=>{requests.push({url,opts});
   if(url==='/api/account/draft'){await wait(saveDelay);const body=JSON.parse(opts.body);assert.equal(body._revision,revision);delete body._revision;stored=body;return new Response(JSON.stringify({ok:true,revision:++revision}));}
   if(url==='/api/account')return new Response(JSON.stringify({email:'tester@example.org',active,purchased:true,expires_at:expires,server_time:Math.floor(Date.now()/1000),revision,draft:stored}));
   throw new Error('Unexpected request '+url);
  };
 }});
 await wait(400);dom.window.eval(source);await wait(1000);
 return {dom,w:dom.window,requests,problems,stored:()=>stored};
}
(async()=>{
 let t=await boot({draft:{fields:{name:{value:'Saved Test Name'},cvLang:{value:'Deutsch'}},storage:{chyper_experience_v1:JSON.stringify([{role:'Engineer',company:'Sample Company'}])},photo:null}});
 try{
  assert.equal(t.w.document.getElementById('name').value,'Saved Test Name');
  assert.equal(t.w.document.getElementById('cvLang').value,'Deutsch');
  assert.equal(t.w.document.getElementById('unlockedBtn').style.display,'block');
  assert.match(t.w.document.getElementById('accountBar').textContent,/tester@example.org/);
  assert.equal(t.w.document.querySelector('#screen3').inert,false);
  console.log('PASS active account restores draft, CV language and download');
  console.log('Page diagnostics:',JSON.stringify([...new Set(t.problems)]));
 }finally{t.dom.window.close();}
 t=await boot({active:false,expires:Math.floor(Date.now()/1000)-1});
 try{
  assert.equal(t.w.document.querySelector('#screen3').inert,true);
  assert.equal(t.w.document.getElementById('unlockedBtn').style.display,'none');
  console.log('PASS expired access locks editor and download');
 }finally{t.dom.window.close();}
 t=await boot({saveDelay:600});
 try{
  const input=t.w.document.getElementById('name');input.value='First edit';input.dispatchEvent(new t.w.Event('input',{bubbles:true}));
  await wait(1100);input.value='Second edit during save';input.dispatchEvent(new t.w.Event('input',{bubbles:true}));
  await wait(1800);
  assert.equal(t.stored().fields.name.value,'Second edit during save');
  assert.equal(t.requests.filter(r=>r.url==='/api/account/draft').length,2);
  console.log('PASS edits made during save are saved in sequence');
 }finally{t.dom.window.close();}
 t=await boot({expires:Math.floor(Date.now()/1000)+3});
 try{
  await wait(3200);
  assert.equal(t.w.document.querySelector('#screen3').inert,true);
  console.log('PASS editor locks without page reload at expiration');
 }finally{t.dom.window.close();}
})().catch(e=>{console.error(e);process.exit(1)});

