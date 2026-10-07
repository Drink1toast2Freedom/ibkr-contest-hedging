const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const ids=new Set([...fs.readFileSync('hedge/web/index.html','utf8').matchAll(/id="([^"]+)"/g)].map(x=>x[1]));
const nodes=new Map([...ids].map(id=>[id,{content:'old-token',dataset:{},classList:{toggle(){}},addEventListener(){}}]));
const posts=[];
let reject=true;
const response=(status,text)=>({status,ok:status>=200&&status<300,text:async()=>text,json:async()=>JSON.parse(text)});
const context=vm.createContext({
  console,Intl,Date,JSON,Error,AbortSignal,setInterval(){},
  DOMParser:class{parseFromString(){return{querySelector:()=>({content:'new-token'})};}},
  document:{querySelector:()=>({content:'old-token'}),querySelectorAll:()=>[],
    getElementById:id=>nodes.get(id)||null,addEventListener(){}},
  fetch:async(path,options)=>{
    if(path==='/api/state')return response(200,'{}');
    if(path==='/')return response(200,'fresh authorization page');
    posts.push(options.headers['X-Control-Token']);
    if(options.headers['X-Control-Token']==='old-token')return response(403,'Local control token required');
    if(reject)return response(409,JSON.stringify({detail:'Mandatory holdings missing'}));
    return response(200,JSON.stringify({mode:'PAUSED'}));
  }
});
vm.runInContext(fs.readFileSync('hedge/web/app.js','utf8'),context);
vm.runInContext('refresh=async()=>{}',context);
(async()=>{
  await vm.runInContext('control("paper")',context);
  assert.deepEqual(posts,['old-token','new-token']);
  assert.equal(nodes.get('notice').textContent,'Mandatory holdings missing');
  assert.equal(nodes.get('notice').hidden,false);
  reject=false;
  await vm.runInContext('control("pause")',context);
  assert.match(nodes.get('notice').textContent,/Pause accepted/);
  assert.match(nodes.get('notice').textContent,/disarmed/);
  await vm.runInContext('control("shadow")',context);
  assert.match(nodes.get('notice').textContent,/Shadow mode accepted/);
  console.log('Dashboard controls: expired token refresh, server rejection, pause and shadow feedback passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
