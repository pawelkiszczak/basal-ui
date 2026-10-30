// Inspect view.
'use strict';
// Inspect: basal-viz embedded at /viz/ for a downloaded bf16 model (basal_ui/inspect.py decides: inspect_reason)
const inspectableModel=m=>!m.inspect_reason;
let inspectSignature='', inspectReady=false, inspectCalibrationSequence=0;
function renderInspectModels() {
  const select=$('#inspectModel'), current=select.value||stored('basal-ui.inspectModel')||'', rows=modelData.filter(m=>m.downloaded);
  const signature=JSON.stringify(rows.map(m=>m.id));
  if(signature===inspectSignature)return;
  inspectSignature=signature;
  select.innerHTML=rows.map(m=>`<option value="${esc(m.id)}"${inspectableModel(m)?'':` disabled title="${esc(m.inspect_reason)}"`}>${esc(m.id)}${inspectableModel(m)?'':` — ${t('basal-viz reads bf16 weights')}`}</option>`).join('')||`<option value="">${t('Download a bf16 model first')}</option>`;
  const ok=rows.filter(inspectableModel).map(m=>m.id);
  select.value=ok.includes(current)?current:ok[0]||'';
  loadInspectCalibrations();
}
async function loadInspectCalibrations() {
  const id=$('#inspectModel').value, sel=$('#inspectCalibration'), sequence=++inspectCalibrationSequence;
  sel.innerHTML=`<option value="">${t('shipped calibration')}</option>`;
  sel.disabled=!id;
  if(!id)return;
  try{
    const d=await api(`/api/calibrations?id=${encodeURIComponent(id)}`);
    if(sequence!==inspectCalibrationSequence||id!==$('#inspectModel').value)return;
    sel.insertAdjacentHTML('beforeend',d.files.map(f=>{const count=f.fitted_on?.decisions;return `<option value="${esc(f.file)}"${f.file===d.selected?' selected':''}>${esc(new Date(f.when*1000).toLocaleString())} · ${count==null?'?':tp(count,'{n} decisions','{n} decisions')}</option>`;}).join(''));
  }catch{}finally{
    if(sequence===inspectCalibrationSequence&&id===$('#inspectModel').value)sel.disabled=false;
  }
}
$('#inspectModel').onchange=()=>{store('basal-ui.inspectModel',$('#inspectModel').value);loadInspectCalibrations();};
$('#startInspect').onclick=()=>act(async()=>{const orders=$('#inspectOrders').value;await api('/api/inspect',{method:'POST',body:JSON.stringify({id:$('#inspectModel').value,calibration:$('#inspectCalibration').value||null,orders:orders?Number(orders):null})});toast(t('Inspector loading'));await refreshStatus();});
$('#stopInspect').onclick=()=>act(async()=>{await api('/api/inspect',{method:'DELETE'});await refreshStatus();});
function renderInspectState(state, job) {
  const loading=Boolean(job?.running), ready=state?.state==='ready', frame=$('#vizFrame');
  $('#inspectState').textContent=loading?t('loading {model}…',{model:shortName(job.model)}):ready?t('{status}: {model} · {mode} on {device} · {orders}',{status:t('ready'),model:state.model,mode:state.mode,device:state.device,orders:tp(state.orders,'{n} option order','{n} option orders')}):job?.phase==='error'?t('Failed: {message}',{message:job.error}):t('idle');
  $('#stopInspect').hidden=!(ready||loading); $('#startInspect').disabled=loading||!$('#inspectModel').value;
  if(ready&&!inspectReady){frame.dataset.loaded='';frame.onload=()=>{frame.dataset.loaded='1';};frame.src='/viz/';}
  if(!ready&&inspectReady){frame.src='about:blank';$('#inspectNotice').hidden=true;}
  frame.hidden=!ready; inspectReady=ready;
}
// What the inspector would explain of a Playground decision: the request it can explain and how that differs from
// the served one, or why it cannot (another model, media, nothing but task questions).
function inspectHandoff(request) {
  const st=statusCache?.inspect||{}, served=(statusCache?.models||[]).find(m=>m.id===request.model);
  const variant=id=>modelData.find(m=>m.id===id), sv=variant(request.model), iv=variant(st.model);
  if(request.media)return {blocked:t('basal-viz reads text only; remove the media to inspect this request')};
  if(st.model!==request.model&&!(sv&&iv&&sv.family===iv.family&&sv.tier===iv.tier))
    return {blocked:t('the inspector runs {inspector}, this decision came from {model}: start the inspector with the bf16 weights of {weights}',{inspector:st.model,model:request.model,weights:sv?`${sv.family} ${sv.tier}`:request.model})};
  const body=structuredClone(request), notes=[];
  for(const key of ['model','think','think_text','early_exit'])delete body[key];
  const tasks=Object.entries(body.questions||{}).filter(([,q])=>TASK_TYPES.includes(q?.type)).map(([n])=>n);
  tasks.forEach(n=>delete body.questions[n]);
  if(!Object.keys(body.questions||{}).length)return {blocked:t('basal-viz explains decision questions; this request has only task questions')};
  if(tasks.length)notes.push(t('{questions}: task questions are left out (basal-viz explains the decision questions)',{questions:tasks.join(', ')}));
  if(st.model!==request.model)notes.push(t('weights: served {label} ({model}), explained bf16 ({inspector})',{label:sv.label,model:request.model,inspector:st.model}));
  if(served?.mode&&served.mode!==st.mode)notes.push(t('engine: served {mode}, explained {inspector} on {device}',{mode:served.mode,inspector:st.mode,device:st.device}));
  const orders=served?.entry?.orders;
  if(orders!=null&&String(orders)!==String(st.orders))notes.push(t('option orders: served {orders}, explained {inspector}{match}',{orders,inspector:st.orders,match:['1','2'].includes(String(orders))?` (${t('start the inspector with {n} option {order} to match',{n:orders,order:String(orders)==='1'?t('order'):t('orders')})})`:''}));
  if(served?.entry&&JSON.stringify(served.entry.temperature_per_prim||{})!==JSON.stringify(st.temperatures||{}))
    notes.push(t('calibration: the served temperatures differ from the inspector’s (choose the calibration the model is served with)'));
  if(served?.options?.think&&request.think!=='off')notes.push(t('thinking: explained without the reasoning (the first readout)'));
  if(request.early_exit&&request.early_exit!=='off')notes.push(t('early exit {policy}: explained at the final layer',{policy:request.early_exit}));
  return {body,notes};
}
function inspectDecision(request) {
  if(statusCache?.inspect?.state!=='ready'){toast(t('Start the inspector for {model} first',{model:request.model||t('a model')}),'error');showView('inspect',true);return;}
  const handoff=inspectHandoff(request), notice=$('#inspectNotice');
  if(handoff.blocked){toast(handoff.blocked,'error');return;}
  notice.className=handoff.notes.length?'inspect-notice differs':'inspect-notice same';
  notice.innerHTML=handoff.notes.length?`<strong>${t('This explanation differs from the served decision of {model}:',{model:esc(request.model)})}</strong><ul>${handoff.notes.map(n=>`<li>${esc(n)}</li>`).join('')}</ul>`:t('Same model, engine, option orders and calibration as the served decision of {model}.',{model:esc(request.model)});
  notice.hidden=false;
  showView('inspect',true);
  const frame=$('#vizFrame'), send=()=>frame.contentWindow.postMessage({request:handoff.body},location.origin);
  if(frame.dataset.loaded==='1')send(); else frame.addEventListener('load',send,{once:true});
}
