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
  await vm.runInContext('renderRecoveryAvailability({mode:"shadow",promotion:{eligible:false,independent_test_windows:0,status:"Validation incomplete",blockers:["Missing scores <script>"]}})',context);
  assert.equal(nodes.get('recovery-enable').disabled,true);
  assert.match(nodes.get('recovery-availability').textContent,/0\/20/);
  assert.match(nodes.get('recovery-validation').innerHTML,/&lt;script&gt;/);
  await vm.runInContext('renderRecoveryAvailability({mode:"shadow",promotion:{eligible:true,independent_test_windows:20,status:"Validation passed",blockers:[]}})',context);
  assert.equal(nodes.get('recovery-enable').disabled,false);
  await vm.runInContext('renderRecoveryAvailability({mode:"paper",promotion:{eligible:true,independent_test_windows:20}})',context);
  assert.equal(nodes.get('recovery-enable').disabled,true);
  const timing=vm.runInContext('solverSummary({total_ms:2143,accepted_stage:"primary",ranking_complete:false})',context);
  assert.match(timing,/2\.14s/);
  assert.match(timing,/ranking incomplete/);
  assert.equal(vm.runInContext('solverSummary(undefined)',context),'');
  await vm.runInContext('renderContinuity({continuity:{automatic_resume_pending:true,wait:{reason:"Stale UAL book"}}})',context);
  assert.equal(nodes.get('continuity-status').hidden,false);
  assert.match(nodes.get('continuity-status').textContent,/resumes automatically/);
  await vm.runInContext('renderContinuity({continuity:{automatic_resume_pending:false,pause_reason:"Operator requested pause"}})',context);
  assert.match(nodes.get('continuity-status').textContent,/Automatic resume is disabled/);
  console.log('Dashboard controls: expired token refresh, server rejection, pause and shadow feedback passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
