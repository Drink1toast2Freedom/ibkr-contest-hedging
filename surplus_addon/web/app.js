'use strict';
const $ = id => document.getElementById(id);
let token = document.querySelector('meta[name="control-token"]').content;
const money = v => Number.isFinite(v) ? new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'}).format(v) : 'Unavailable';
const number = v => Number.isFinite(v) ? v.toFixed(3) : 'Uncalibrated';
const time = v => v ? new Date(v).toLocaleString('en-US',{timeZone:'America/New_York',timeZoneName:'short'}) : 'Unavailable';
const text = (id, v) => { $(id).textContent = v; };
let busy = false;
async function post(path, body) {
  for(let attempt=0;attempt<2;attempt++){
    const response = await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Control-Token':token},body:JSON.stringify(body)});
    if(response.status===403&&attempt===0){
      const page=await fetch('/');
      const fresh=new DOMParser().parseFromString(await page.text(),'text/html').querySelector('meta[name="control-token"]');
      if(fresh){token=fresh.content;continue;}
    }
    let result;
    try{result=await response.json();}catch{throw new Error('Research control rejected. Refresh this dashboard.');}
    if(!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Invalid research request');
    return result;
  }
  throw new Error('Research control rejected. Refresh this dashboard.');
}
function cell(row,value){const td=document.createElement('td');td.textContent=value;row.append(td);}
function rows(id,values,columns){const el=$(id);el.replaceChildren();for(const item of values){const tr=document.createElement('tr');for(const get of columns)cell(tr,get(item));el.append(tr);}}
function draw(s) {
  const d=s.decision, p=d.proposal, st=d.stats||{}, b=d.budget||{}, demo=s.source==='synthetic rehearsal';
  $('demo-banner').hidden=!demo;text('state',d.state);text('reason',d.reason);
  text('source',`${s.source} · ${d.account||'Waiting for account'} · ${time(d.at)} · main mode ${d.main_mode||'unavailable'}`);
  $('simulate').disabled=!(demo&&s.enabled&&d.state==='PROPOSED');$('advance').disabled=!demo;
  const metrics=[['NAV estimate',money(d.nav),'Accrued interest already included'],['Closing target',money(d.target),d.target_estimated?'Approved approximate benchmark':'Official rule / synthetic in demo'],
    ['Current gap',money(d.current_gap),'NAV minus closing target'],['Projected closing gap',money(d.predicted_closing_gap),'Before any NEW cycle'],
    ['Movement buffer',money(d.buffer),'Forecast tail and mark uncertainty'],['Needed reduction',money(d.needed_reduction),'After the buffer'],
    ['Required speed',number(d.required_loss_per_second)+' $/s','Until the new-entry cutoff'],['Simulated net speed',number(st.observed_loss_per_second)+' $/s','Completed synthetic cycles only']];
  $('metrics').replaceChildren();for(const [label,value,note]of metrics){const div=document.createElement('div');div.className='metric';for(const [tag,t]of [['span',label],['strong',value],['small',note]]){const e=document.createElement(tag);e.textContent=t;div.append(e);}$('metrics').append(div);}
  text('proposal',p?`${p.symbol}: buy and restore ${p.quantity} shares. Entry cap ${p.buy_cap}; exit reference floor ${p.sell_floor}. Modeled reduction ${money(p.modeled_loss)}; reserve ${money(p.expense_reserve)}. Expected score improvement ${money(p.expected_score_improvement)}. Modeled overshoot ${(p.overshoot_probability*100).toFixed(2)}%.`: 'No eligible proposal. '+d.reason);
  $('checks').replaceChildren();for(const msg of [p?`${p.path_checked_holdings} partial-fill holdings independently checked.`:'No proposal can be simulated.',...(d.assumptions||[])]){const li=document.createElement('li');li.textContent=msg;$('checks').append(li);}
  $('capacity').replaceChildren();for(const [label,value] of [['Expense budget left',money(b.expense_remaining)],['Orders left',b.orders_remaining??'Unavailable'],['Turnover left',money(b.turnover_remaining)],['Confirmed simulated loss',money(st.confirmed_loss)],['Modeled feasible capacity',money(d.capacity?.modeled)],['Empirically supported capacity','Uncalibrated'],['Modeled cycle speed',number(p?.modeled_loss_per_second)+' $/s'],['Unremovable modeled surplus',money(d.projected_unremovable_surplus)]]){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent=value;$('capacity').append(dt,dd);}
  if(!busy){$('model').replaceChildren();for(const model of s.ai.models){const option=document.createElement('option');option.value=model;option.textContent=model;option.selected=model===s.ai.model;$('model').append(option);}}
  text('ai-status',`${s.ai.enabled?'Enabled':'Disabled'} · ${s.ai.configured?'Local Google key configured':'Local Google key unavailable'} · ${s.ai.status}`);
  text('advice',s.advice?`${s.advice_stale?'Stale explanation · ':''}${s.advice.explanation}`:'AI advice is optional and off by default. Numerical monitoring never waits for AI.');
  rows('cycles',s.cycles||[],[c=>time(c.at),c=>c.con_id,c=>c.quantity,c=>money(Number(c.loss)),c=>money(Number(c.fees)),c=>c.evidence]);
  rows('history',s.history||[],[d=>time(d.at),d=>d.state,d=>money(d.predicted_closing_gap),d=>d.reason]);
  text('provenance',`Compute ${d.compute_ms??'—'} ms · snapshot ${d.snapshot_id||'unavailable'} · scenario ${d.scenario_id||'unavailable'} · config ${d.config_hash||'unavailable'}`);
}
async function refresh(){if(busy)return;try{const r=await fetch('/api/state');if(!r.ok)throw new Error('Research dashboard unavailable');draw(await r.json());}catch(e){text('message',e.message);$('simulate').disabled=true;}}
async function command(path,body){if(busy)return;busy=true;text('message','Working…');try{await post(path,body);text('message','Research setting applied. No broker orders.');}catch(e){text('message',e.message);}finally{busy=false;await refresh();}}
for(const button of document.querySelectorAll('[data-action]'))button.addEventListener('click',()=>command('/api/control',{action:button.dataset.action}));
$('enable-ai').addEventListener('click',()=>command('/api/ai/settings',{model:$('model').value,enabled:true}));
$('disable-ai').addEventListener('click',()=>command('/api/ai/settings',{model:$('model').value,enabled:false}));
refresh();setInterval(refresh,2000);
