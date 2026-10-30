// Settings (auto-load; theme, language, memory, models directory, updates and presets keep their handlers in core.js,
// i18n.js, shell.js, models.js and playground.js) and the Overview's first-run checklist.
'use strict';
$('#autoLoad').addEventListener('change',()=>act(async()=>{
  const value=$('#autoLoad').checked;
  try{await api('/api/serve',{method:'PUT',body:JSON.stringify({auto_load:value})});}
  catch(e){setChecked($('#autoLoad'),!value);throw e;}
  if(serveData)serveData.auto_load=value;
  toast(value?t('The loaded models load again when basal-ui starts'):t('basal-ui starts without loading models'));
}));
setChecked($('#singleKeyShortcuts'),singleKeyShortcuts());
$('#singleKeyShortcuts').addEventListener('change',e=>store('basal-ui.single-keys',e.target.checked?'on':'off'));
// the models directory changes only while nothing is loaded (Fleet.while_idle): say so before Save, not after
function renderModelsDirLock() {
  const locked=Boolean((statusCache?.models||[]).length||Object.keys(statusCache?.downloads||{}).length);
  $('#modelsDir').readOnly=locked; $('#saveModelsDir').disabled=$('#defaultModelsDir').disabled=locked; $('#modelsDirLocked').hidden=!locked;
  const input=$('#modelsDir'), ids=(input.getAttribute('aria-describedby')||'').split(' ').filter(x=>x&&x!=='modelsDirLocked');
  input.setAttribute('aria-describedby',(locked?['modelsDirLocked',...ids]:ids).join(' '));
}
// first run: hardware detected → a recommended model downloaded and loaded → an example in the Playground → an app
// connected; hidden once every step is done (remembered) or dismissed, in this browser
const CHECK='<svg viewBox="0 0 16 16" width="14" height="14"><path d="m3 8.5 3 3 7-7" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
function firstRunSteps() {
  const ready=readyModels(), suggested=(catalogData?.suggestions||[]).map(s=>modelData.find(m=>m.id===s.id)).find(m=>m?.runnable);
  const tried=stored('basal-ui.tried')==='1'||Object.values(statsData.models||{}).some(x=>Number(x.requests)>0);
  const connected=stored('basal-ui.connected')==='1'||Boolean(statusCache?.serving?.running);
  const entry=suggested&&loadedEntry(suggested.id), loading=(statusCache?.models||[]).find(m=>m.state!=='ready'&&m.state!=='error');
  const modelAction=ready.length?'':loading?html`<span class="chip">${t(loading.state)}</span>`:suggested&&!entry
    ?html`<button type="button" class="load-model primary" data-id="${suggested.id}">${suggested.downloaded?t('Load'):t('Download & load')}</button>`
    :html`<a class="btn" href="#discover">${t('Discover models')}</a>`;
  return [
    {done:Boolean(hw),title:t('Hardware detected'),text:hw?[hw.device_name,gb(hw.memory_bytes)].filter(Boolean).join(' · '):''},
    {done:ready.length>0,title:t('Download and load a recommended model'),text:ready.length?shortName(ready[0].id):suggested?`${suggested.family} ${suggested.tier} · ${suggested.label} · ${t('est. memory')} ${gb(suggested.est_memory_bytes)}`:t('No suggested model fits right now.'),action:modelAction},
    {done:tried,title:t('Try an example in the Playground'),text:t('Send one of the bundled example requests and read the Decision.'),action:ready.length?html`<button type="button" class="btn" data-first-run="example">${t('Open an example')}</button>`:''},
    {done:connected,title:t('Connect your app'),text:t('Turn on the API server and copy the endpoint or a snippet.'),action:html`<button type="button" class="btn" data-first-run="connect">${t('Open API server')}</button>`},
  ];
}
function renderFirstRun() {
  const card=$('#firstRun');
  if(['dismissed','done'].includes(stored('basal-ui.firstrun'))||!catalogData||!statusCache){card.hidden=true;return;}
  const steps=firstRunSteps();
  if(steps.every(s=>s.done)){store('basal-ui.firstrun','done');card.hidden=true;return;}
  const markup=steps.map((s,i)=>html`<li class="first-run-step${s.done?' done':''}"><span class="step-mark" aria-hidden="true">${s.done?raw(CHECK):i+1}</span><div class="step-body"><strong>${s.title}</strong> <span class="sr-only">${s.done?t('done'):t('to do')}</span><span class="muted">${s.text}</span></div>${s.done||!s.action?'':html`<div class="controls">${s.action}</div>`}</li>`.text).join('');
  card.hidden=false;
  // unchanged across status polls: kept as is; a re-render (the button became a "downloading" chip) keeps the focus
  renderKeepingFocus($('#firstRunSteps'),markup);
}
$('#firstRunDismiss').onclick=()=>{store('basal-ui.firstrun','dismissed');$('#firstRun').hidden=true;$('#pageTitle').focus();};
$('#firstRunSteps').addEventListener('click',e=>{
  const b=e.target.closest('[data-first-run]');
  if(!b)return;
  if(b.dataset.firstRun==='connect')return showView('api',true);
  showView('playground',true);
  const select=$('#examples'), first=examples.findIndex(x=>!(x.request?.media||[]).length);
  if(!select.value&&first>=0){select.value=String(first);select.dispatchEvent(new Event('change'));}
});
