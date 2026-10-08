const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync('surplus_addon/web/index.html','utf8');
const node=()=>({dataset:{},content:'token',children:[],listeners:{},textContent:'',value:'gemini-3.8-flash',
  addEventListener(event,fn){this.listeners[event]=fn;},append(...items){this.children.push(...items);},
  replaceChildren(){this.children=[];}});
const nodes=new Map([...html.matchAll(/id="([^"]+)"/g)].map(x=>[x[1],node()]));
const buttons=[...html.matchAll(/data-action="([^"]+)"/g)].map(x=>Object.assign(node(),{dataset:{action:x[1]}}));
const posts=[];
let expire=false;
const state={source:'synthetic rehearsal',enabled:true,execution_enabled:false,
  decision:{state:'PROPOSED',reason:'Synthetic proposal',proposal:{quantity:10},stats:{},budget:{}},
  ai:{models:['gemini-3.8-flash'],model:'gemini-3.8-flash'},history:[],cycles:[]};
const context=vm.createContext({console,Intl,Date,JSON,Error,Number,setInterval(){},
  DOMParser:class{parseFromString(){return{querySelector:()=>({content:'fresh-token'})};}},
  document:{querySelector:()=>({content:'token'}),querySelectorAll:()=>buttons,
    getElementById:id=>nodes.get(id),createElement:()=>node()},
  fetch:async(path,options)=>{
    if(path==='/')return{ok:true,text:async()=>'<meta name="control-token" content="fresh-token">'};
    if(!options)return{ok:true,json:async()=>state};
    posts.push([path,JSON.parse(options.body),options.headers['X-Control-Token']]);
    if(expire&&options.headers['X-Control-Token']==='token')return{ok:false,status:403};
    return{ok:true,json:async()=>state};
  }});
vm.runInContext(fs.readFileSync('surplus_addon/web/app.js','utf8'),context);
vm.runInContext('refresh=async()=>{}',context);
(async()=>{
  for(const button of buttons)await button.listeners.click();
  assert.deepEqual(posts.map(p=>p[1].action),['start','pause','main_feed','new_demo','simulate_cycle','advance_demo']);
  assert(posts.every(p=>p[0]==='/api/control'&&p[2]==='token'));
  assert(!posts.some(p=>/paper|trade|order/.test(p[1].action)));
  await nodes.get('enable-ai').listeners.click();
  assert.deepEqual(posts.at(-1)[1],{model:'gemini-3.8-flash',enabled:true});
  expire=true;
  await vm.runInContext('command("/api/control",{action:"pause"})',context);
  assert.equal(posts.at(-2)[2],'token');assert.equal(posts.at(-1)[2],'fresh-token');
  state.decision.state='WAITING';context.fakeState=state;
  vm.runInContext('draw(fakeState)',context);
  assert.equal(nodes.get('simulate').disabled,true);
  assert.equal(nodes.get('demo-banner').hidden,false);
  state.source='main app read-only feed';state.advice={explanation:'<script>trade()</script>'};
  vm.runInContext('draw(fakeState)',context);
  assert.equal(nodes.get('demo-banner').hidden,true);
  assert.equal(nodes.get('advice').textContent,'<script>trade()</script>');
  console.log('Surplus UI controls, source labels, simulation gating and text rendering passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
