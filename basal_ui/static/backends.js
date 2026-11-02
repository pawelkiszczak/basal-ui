'use strict';
let backendInventory=null, backendTimer=null, testingBackend=null;
let addonInventory=null, addonLogGeneration=null, addonLogAfter=0, addonLogLines=[], addonLogFetching=false;
function ensureAddonSection(){
  if($('#engineAddons'))return;
  $('#backendInventory').insertAdjacentHTML('beforebegin',`<section class="card" id="engineAddons"><div class="section-head"><h3>${t('Engine add-ons')}</h3><button type="button" id="refreshAddons" data-variant="outline">${t('Refresh add-ons')}</button></div><p class="muted">${t('Install supported extras into this app’s current Engine environment. All models and jobs must be stopped; restart basal-ui after installation.')}</p><div id="addonEnvironment" class="muted"></div><div id="addonInventory" aria-live="polite"></div><div id="addonJob" aria-live="polite"></div></section>`);
  $('#refreshAddons').onclick=()=>loadAddons();
  $('#addonInventory').onclick=e=>act(()=>addonAction(e));
  $('#addonJob').onclick=e=>act(()=>addonAction(e));
}
async function loadAddons(){
  ensureAddonSection();
  try{
    addonInventory=await api('/api/addons');
    renderAddons();
    await renderAddonJob(statusCache);
  }catch(e){$('#addonInventory').innerHTML=`<p class="error-box">${esc(e.message)}</p>`;}
}
function renderAddons(){
  if(!addonInventory)return;
  const env=addonInventory.environment||{}, job=statusCache?.jobs?.['engine-addon'];
  $('#addonEnvironment').textContent=env.python?`${t('Python environment')}: ${env.python}`:'';
  $('#addonInventory').innerHTML=addonInventory.error?`<p class="error-box">${esc(ts(addonInventory.error,addonInventory.error_code,addonInventory.error_params))}</p>`:(addonInventory.extras||[]).map(x=>{
    const restartRequired=Boolean(x.installed&&!job?.running&&job?.result?.restart_required&&job.result.extra===x.id);
    const name=t(x.name),status=x.installed?(restartRequired?t('Installed; restart to use'):t('Installed')):x.compatible?t('Available to install'):t('Unavailable');
    return `<article class="backend-card"><div class="section-head"><h4>${esc(name)}</h4><span class="badge ${x.installed?'fit-fits':x.compatible?'fit-tight':'fit-unknown'}">${esc(status)}</span></div><p>${esc(ts(x.reason,x.reason_code,x.reason_params)||'')}</p><p class="muted">${t('Engine {version} · source {source}',{version:x.version,source:x.source})}</p>${x.command?`<details><summary>${t('Install command')}</summary><pre>${esc(x.command)}</pre></details>`:''}${x.compatible&&!x.installed?`<button type="button" data-addon-install="${esc(x.id)}" ${job?.running?'disabled':''}>${t('Install {name}',{name})}</button>`:''}</article>`;
  }).join('');
}
async function addonAction(e){
  const install=e.target.closest('[data-addon-install]');
  if(install){
    const extra=install.dataset.addonInstall;
    const item=addonInventory.extras.find(x=>x.id===extra), name=t(item.name);
    if(!await confirmDialog({title:t('Install {name}?',{name}),description:t('This changes packages in the current Engine environment. Stop all models and jobs first. Basal-ui will not restart automatically; restart it after installation.'),confirmLabel:t('Install and restart later')}))return;
    try{
      await api('/api/addons',{method:'POST',body:JSON.stringify({extra,confirmed:true})});
      addonLogGeneration=null;addonLogAfter=0;addonLogLines=[];
      await refreshStatus();await renderAddonJob(statusCache);
    }catch(error){$('#addonJob').innerHTML=`<p class="error-box">${esc(error.message)}</p>`;}
    return;
  }
  if(e.target.closest('[data-addon-cancel]')){
    try{await api('/api/addons',{method:'DELETE'});await refreshStatus();await renderAddonJob(statusCache);}
    catch(error){$('#addonJob').innerHTML=`<p class="error-box">${esc(error.message)}</p>`;}
  }
}
let addonInventoryCheckedSignature=null;
async function renderAddonJob(state=statusCache){
  if($('#backends').hidden)return;
  ensureAddonSection();
  const job=state?.jobs?.['engine-addon'];
  if(!job){$('#addonJob').innerHTML='';return;}
  const signature=`${job.started}:${job.result?.extra||''}:${job.phase}`;
  if(!job.running&&job.result?.restart_required&&addonInventoryCheckedSignature!==signature){
    addonInventoryCheckedSignature=signature;
    try{addonInventory=await api('/api/addons');}catch{}
  }
  if(!addonLogFetching){
    addonLogFetching=true;
    try{
      const logs=await api(`/api/logs?source=${encodeURIComponent('job:engine-addon')}&after=${addonLogAfter}${addonLogGeneration?`&generation=${encodeURIComponent(addonLogGeneration)}`:''}`);
      if(addonLogGeneration!==logs.generation){addonLogGeneration=logs.generation;addonLogAfter=0;addonLogLines=[];}
      const lines=logs.lines||[];
      addonLogLines.push(...lines);
      if(lines.length)addonLogAfter=lines[lines.length-1].seq;
      if(addonLogLines.length>1000)addonLogLines=addonLogLines.slice(-1000);
    }catch{}
    finally{addonLogFetching=false;}
  }
  renderAddons();
  const result=job.result?.restart_required?`<p class="fit-fits">${t('Installation complete. Restart basal-ui before using this add-on.')}</p>`:'';
  $('#addonJob').innerHTML=`<article class="backend-card"><div class="section-head"><strong>${esc(t('Engine add-on installation'))} · ${esc(ts(job.phase,job.phase_code,job.phase_params))}</strong>${job.running?`<button type="button" data-addon-cancel>${t('Cancel installation')}</button>`:''}</div>${job.error?`<p class="error-box">${esc(job.error)}</p>`:''}${result}<pre class="log-output">${esc(addonLogLines.map(x=>x.text).join('\n'))}</pre></article>`;
}
document.addEventListener('app:status',e=>renderAddonJob(e.detail));
const backendStatus={installed:t('Installed'),not_installed:t('Not installed'),needs_attention:t('Needs attention'),unavailable:t('Unavailable on this machine')};
async function loadBackends(refresh=false){
  $('#refreshBackends').disabled=true;$('#backendError').hidden=true;
  if(!backendInventory)$('#backendInventory').textContent=t('Checking backend dependencies and devices…');
  try{
    backendInventory=await api(`/api/backends${refresh?'?refresh=true':''}`);
    const env=backendInventory.environment;
    $('#backendEnvironment').textContent=`${backendInventory.hardware.device_name} · Python ${env.version} · ${env.python}`;
    renderBackends();renderBackendTests(backendInventory.tests);
    await loadAddons();
    scheduleBackendTests();
  }catch(e){$('#backendError').textContent=e.message;$('#backendError').hidden=false;}
  finally{$('#refreshBackends').disabled=false;}
}
function renderBackends(){
  if(!backendInventory)return;
  const entries=backendInventory.backends.filter(b=>b.compatible||$('#showUnavailableBackends').checked);
  $('#backendInventory').innerHTML=entries.map(b=>{const reason=ts(b.reason,b.reason_code,b.reason_params),note=ts(b.setup.note,b.setup.note_code,b.setup.note_params),status=ts(backendStatus[b.status],b.status_code,b.status_params);return `<article class="backend-card"><div class="section-head"><h3>${esc(b.name)}</h3><span class="badge ${b.status==='installed'?'fit-fits':b.status==='needs_attention'?'fit-tight':'fit-unknown'}">${esc(status)}</span></div><p>${esc(reason)}</p><p class="muted">${Object.entries(b.versions).map(([p,v])=>`${esc(p)}: ${esc(v||t('not installed'))}`).join(' · ')}</p><div class="muted">${t('Engine modes (availability depends on model and machine)')}</div><div class="chips">${b.modes.map(m=>`<span class="chip">${esc(m)}</span>`).join('')}</div><details><summary>${t('Setup guidance')}</summary><p>${esc(note)}</p>${b.setup.command?`<pre>${esc(b.setup.command)}</pre><button type="button" data-backend-copy="${esc(b.id)}">${t('Copy setup command')}</button>`:''}<p><a href="${esc(b.documentation)}" target="_blank" rel="noopener">${t('Backend documentation')}</a></p></details><div class="controls"><button type="button" data-backend-models="${esc(b.id)}" ${b.models.length?'':'disabled'}>${tp(b.models.length,'View matching models ({n})','View matching models ({n})')}</button><button type="button" data-backend-test="${esc(b.id)}" ${b.status==='installed'&&b.models.some(m=>m.downloaded)?'':'disabled'}>${t('Test inference…')}</button></div>${b.compatible&&!b.models.some(m=>m.downloaded)?`<p class="muted">${t('Download a compatible model from Models before testing.')}</p>`:''}</article>`}).join('');
}
$('#refreshBackends').onclick=()=>loadBackends(true);
$('#showUnavailableBackends').onchange=renderBackends;
$('#backendInventory').onclick=e=>{
  const copy=e.target.closest('[data-backend-copy]'),models=e.target.closest('[data-backend-models]'),test=e.target.closest('[data-backend-test]');
  if(copy){const b=backendInventory.backends.find(x=>x.id===copy.dataset.backendCopy);copyText(b.setup.command,t('Setup command copied'));}
  if(models){
    const b=backendInventory.backends.find(x=>x.id===models.dataset.backendModels);
    backendModelFilter={name:b.name,ids:new Set(b.models.map(m=>m.id))};
    $('#backendModelFilterNotice').hidden=false;$('#backendModelFilterNotice span').textContent=t('Models compatible with {name} after its dependencies are installed.',{name:b.name});
    setChecked($('#olderToggle'),true);setChecked($('#notRunnableToggle'),true);setChecked($('#downloadedToggle'),false);
    rerenderFilters();showView('models',true);selectModelsTab('all');
  }
  if(test)act(()=>openBackendTest(test.dataset.backendTest));
};
$('#clearBackendModelFilter').onclick=()=>{backendModelFilter=null;$('#backendModelFilterNotice').hidden=true;rerenderFilters();};
async function openBackendTest(id){
  testingBackend=backendInventory.backends.find(b=>b.id===id);
  const models=testingBackend.models.filter(m=>m.downloaded);
  $('#backendTestTitle').textContent=t('Test {name}',{name:testingBackend.name});
  $('#backendTestModel').innerHTML=models.map(m=>`<option value="${esc(m.id)}">${esc(m.id)}</option>`).join('');
  $('#backendTestError').hidden=true;$('#startBackendTest').disabled=!models.length;
  updateBackendTestModes();
  const state=await api('/api/status');
  $('#backendUnloadChoices').innerHTML=state.models.length?state.models.map(m=>{
    const idle=m.state==='ready'&&m.health?.reachable===true&&m.health.running===0&&m.health.queued===0;
    return `<label class="backend-unload"><input type="checkbox" value="${esc(m.id)}" data-generation="${esc(m.generation||'')}" ${idle?'':'disabled'}><span>${esc(m.id)} <small class="muted">${idle?t('idle; rechecked before unloading'):t('busy or activity unknown')}</small></span></label>`;
  }).join(''):`<p class="muted">${t('No running models.')}</p>`;
  $('#backendTestDialog').showModal();
}
function updateBackendTestModes(){
  const model=testingBackend.models.find(m=>m.id===$('#backendTestModel').value);
  $('#backendTestMode').innerHTML=(model?.modes||[]).map(m=>`<option>${esc(m)}</option>`).join('');
  updateBackendEstimate();
}
function updateBackendEstimate(){
  const model=testingBackend.models.find(m=>m.id===$('#backendTestModel').value),needed=model?.mode_estimates?.[$('#backendTestMode').value];
  $('#backendTestEstimate').textContent=t('Revision: {revision} · Estimated memory: {memory}. Startup may take up to 30 minutes for compilation; inference has a 2-minute deadline.',{revision:model?.revision||t('current downloaded snapshot (pinned at start)'),memory:needed?gb(needed):t('unknown — test unavailable')});
  $('#startBackendTest').disabled=!needed;
}
$('#backendTestModel').onchange=updateBackendTestModes;$('#backendTestMode').onchange=updateBackendEstimate;
$('#startBackendTest').onclick=async()=>{
  const unload=[...$('#backendUnloadChoices input:checked')].map(e=>({id:e.value,generation:e.dataset.generation}));
  if(unload.length&&!await confirmDialog({title:t('Unload selected models for this test?'),description:t('{models} will remain stopped after the test. Use Restore to reload their previous configuration. Busy models will be refused.',{models:unload.map(m=>m.id).join(', ')}),confirmLabel:t('Unload and test')}))return;
  $('#startBackendTest').disabled=true;
  try{
    await api('/api/backends/tests',{method:'POST',body:JSON.stringify({backend:testingBackend.id,model:$('#backendTestModel').value,mode:$('#backendTestMode').value,unload})});
    $('#backendTestDialog').close();await pollBackendTests();
  }catch(e){$('#backendTestError').textContent=e.message;$('#backendTestError').hidden=false;}
  finally{$('#startBackendTest').disabled=false;}
};
function renderBackendTests(tests){
  $('#backendTests').innerHTML=tests.length?[...tests].reverse().map(test=>{
    const job=statusCache?.jobs?.[test.restoring?'backend-restore':'backend-test'];
    return `<article class="backend-card"><h4>${esc(test.backend)} · ${esc(test.mode)} · ${esc(test.model)}</h4><p>${test.status==='passed'?t('Inference test passed'):esc(ts(test.status,test.status_code,test.status_params))}${test.status==='starting'&&job?.running?` · ${esc(ts(job.phase,job.phase_code,job.phase_params))}`:''}${test.restoring?` · ${t('restoring models')}`:''}</p>${test.result?`<p class="muted">${esc(test.result.elapsed_seconds)} ${t('seconds')} · ${t('revision')} ${esc(test.revision||t('local snapshot'))} · ${t('valid response; not an accuracy or performance certification')}</p>`:''}${test.error?`<p class="error-box">${esc(test.error)}</p>`:''}${test.stopped_models.length?`<p>${t('Stopped models:')}</p><ul>${test.stopped_models.map(m=>`<li>${esc(m.id)} · ${esc(m.mode)}${m.error?` — ${esc(m.error)}`:''}</li>`).join('')}</ul>`:''}<div class="controls"><a href="#logs/job%3A${test.restoring?'backend-restore':'backend-test'}">${t('Latest job logs')}</a>${test.status==='starting'||test.restoring?`<button type="button" data-backend-cancel="${esc(test.id)}">${t('Cancel')}</button>`:''}${test.stopped_models.length&&test.status!=='starting'?`<button type="button" data-backend-restore="${esc(test.id)}" ${test.restoring?'disabled':''}>${t('Restore stopped models')}</button>`:''}</div></article>`;
  }).join(''):`<p class="muted">${t('No tests in this app session. Dependency checks do not verify inference.')}</p>`;
}
$('#backendTests').onclick=e=>{
  const cancel=e.target.closest('[data-backend-cancel]'),restore=e.target.closest('[data-backend-restore]');
  if(cancel)act(async()=>{await api(`/api/backends/tests/${encodeURIComponent(cancel.dataset.backendCancel)}`,{method:'DELETE'});await pollBackendTests();});
  if(restore)act(async()=>{await api(`/api/backends/tests/${encodeURIComponent(restore.dataset.backendRestore)}/restore`,{method:'POST'});await pollBackendTests();});
};
function scheduleBackendTests(){clearTimeout(backendTimer);if(!$('#backends').hidden)backendTimer=setTimeout(pollBackendTests,2000);}
async function pollBackendTests(){
  try{const data=await api('/api/backends/tests');renderBackendTests(data.tests);}catch(e){$('#backendError').textContent=e.message;$('#backendError').hidden=false;}
  scheduleBackendTests();
}
window.addEventListener('backends:view',()=>backendInventory?Promise.all([pollBackendTests(),loadAddons()]):loadBackends());
if(!$('#backends').hidden)loadBackends();
