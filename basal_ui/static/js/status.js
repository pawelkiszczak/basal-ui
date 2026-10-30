// Shared status: loaded models, memory, inference activity; the status poll feeds every view.
'use strict';
// Several models: status rows, memory bar, statistics, benchmarks, History and Logs views.
let selectedModel=localStorage.getItem('basal-ui.model')||'', previousDownloads=new Set(), rowsSignature='', playgroundSignature='';
let statsData={models:{},benchmarks:{}}, statsSignature='', benchSignature='', benchRunning=false;
let historyAfter=null, historyBefore=null, historySource='', historyTimer=null, historyDebounce=null, historyRows=new Map(), historyBrowsingOlder=false;
const historyOpen=new Map(), HISTORY_LIVE_MAX=500;  // rows the live History view keeps while it polls
let logSource='basal-ui', logAfter=0, logGeneration=null, logTimer=null, shownHash=null;
const readyModels=()=>(statusCache?.models||[]).filter(m=>m.state==='ready');
function renderStatus(s) {
  statusCache=s;
  const models=s.models||[], ready=readyModels(), downloads=s.downloads||{}, repos=new Set(Object.keys(downloads));
  const finished=[...previousDownloads].some(repo=>!repos.has(repo)); previousDownloads=repos;
  const signature=JSON.stringify([models.map(m=>[m.id,m.state]),downloads]);
  if(finished)loadModels(); else if(signature!==rowsSignature)rerenderFilters();  // loaded chips and download progress in the model lists
  rowsSignature=signature;
  if(!ready.some(m=>m.id===selectedModel)&&ready.length)selectedModel=ready[0].id;
  if(selectedModel)localStorage.setItem('basal-ui.model',selectedModel);
  const busy=models.some(m=>['starting','downloading'].includes(m.state));
  const live=models.filter(answering).length;
  $('#footerState').textContent=ready.length?`${ready.length} ${t('ready')}${live?` · ${t('{n} answering',{n:live})}`:''}${busy?` · ${t('loading…')}`:''}`:busy?t('loading…'):t('idle');
  $('#footerDot').className=`status-dot${ready.length?' ready':models.some(m=>m.state==='error')?' error':''}${live?' active':''}`;
  $('#footerModel').textContent=models.map(m=>shortName(m.id)).join(', ')||t('No model loaded');
  $('#footerModel').title=models.map(m=>m.id).join(', ')||t('No model loaded');
  $('#footerUrl').textContent=s.serving?.url||''; $('#footerUrl').hidden=!s.serving?.url;
  $('#footerUnload').hidden=!models.length;
  renderMemoryBar(s.memory||{},models);
  renderLoadedRows(models);
  renderInference(s);
  renderPlaygroundModels(ready);
  renderBenchmarkState(s.jobs?.benchmark,ready); renderCalibrationState(s.jobs?.calibrate); renderBatchState(s.jobs?.batch,ready);
  renderInspectState(s.inspect,s.jobs?.inspect);
  renderFirstRun(); renderModelsDirLock();
  announceProgress(s);
  if(serveData)renderServeStatus(s);
  loadStats();
  const jobsRunning=Object.values(s.jobs||{}).some(j=>j.running);
  // every second while anything is answered and while the Overview (which shows it live) is open
  clearTimeout(pollTimer); pollTimer=setTimeout(refreshStatus,busy||repos.size||jobsRunning||models.some(answering)||!$('#view-overview').hidden?1000:5000);
}
function renderMemoryBar(memory, models) {
  const budget=Number(memory.budget_bytes)||0, reserve=Number(memory.reserve_bytes)||0, limit=Math.max(1,budget-reserve);
  const segments=models.map(m=>({label:`${shortName(m.id)}${m.rss_bytes?'':` (${t('estimated')})`}`,value:Number(m.rss_bytes)||Number(m.est_memory_bytes)||0}));
  const used=segments.reduce((sum,x)=>sum+x.value,0), share=used/limit, tone=memory.over?'danger':share>0.85?'warning':'';
  const text=`${gb(used)} / ${gb(limit)}${memory.over?` · ${t('over budget')}`:''}`;
  $('#memoryBar').innerHTML=budget?`<div class="memory-head"><span>${t('Memory')}</span><span>${t('{used} / {limit} (budget {budget}, keep {reserve} free) · available now {available}',{used:gb(used),limit:gb(limit),budget:gb(budget),reserve:gb(reserve),available:gb(memory.available_bytes)})}</span></div>${chartMeter({label:t('Memory'),max:limit,segments:segments.length?segments:[{label:t('Memory'),value:0}],tone,valueText:text,format:gb})}${memory.over?`<p class="reason">${t('over budget')}</p>`:''}`:'';
}
// a loaded model's statistics as stat tiles: requests, average best-answer probability and latency with its recent distribution
function modelStatTiles(stats) {
  if(!stats?.requests)return '';
  const ms=v=>`${num(v,0)} ms`;
  return `<div class="stats loaded-stats"><div class="stat"><span class="stat-label">${t('Requests')}</span><span class="stat-value">${esc(stats.requests)}</span><span class="stat-meta">${stats.errors?tp(stats.errors,'{n} errors','{n} errors'):tp(stats.questions,'{n} question','{n} questions')}</span></div><div class="stat"><span class="stat-label">${t('Avg best-answer prob.')}</span><span class="stat-value">${pct(stats.avg_best_prob)}</span>${stats.avg_best_prob==null?'':chartMeter({label:t('Average probability of the best answer'),max:1,segments:[{label:t('Avg best-answer prob.'),value:stats.avg_best_prob}],valueText:pct(stats.avg_best_prob),format:pct})}</div><div class="stat"><span class="stat-label">${t('Latency p50 / p95')}</span><span class="stat-value">${num(stats.p50_ms,0)} / ${ms(stats.p95_ms)}</span>${chartHistogram({label:t('Latency of the recent requests, in ms'),values:stats.latency_recent_ms||[],bins:10,format:ms})}</div></div>`;
}
// a loaded model answering now: requests basal-ui waits on (inflight) or its basal-serve's running / queued counters
const answering=m=>m.state==='ready'&&((m.inflight||[]).length>0||m.health?.running>0||m.health?.queued>0);
const INFERENCE_SOURCES={playground:'Playground',api:'API server',mcp:'MCP',benchmark:'Benchmark'};
const INFERENCE_JOBS={batch:'Batch',benchmark:'Benchmark',calibrate:'Calibration','backend-test':'Backend test'};
// announce (politely) when a job or a download starts, finishes, fails or is cancelled; not what was already running
const progressSeen=new Map();
function announceProgress(s) {
  const names={...INFERENCE_JOBS,inspect:'Inspect'}, first=!progressSeen.size;
  const states=Object.entries(s.jobs||{}).filter(([,j])=>j).map(([kind,j])=>[kind,j.running?'running':j.phase==='error'?'error':j.phase==='cancelled'?'cancelled':'done',t(names[kind]||kind),j.started]);
  for(const repo of Object.keys(s.downloads||{}))states.push([`download:${repo}`,'running',t('Download of {model}',{model:shortName(repo)})]);
  for(const [key,old] of progressSeen)if(key.startsWith('download:')&&!(s.downloads||{})[key.slice(9)]&&old.state==='running')states.push([key,'done',old.name]);
  for(const [key,state,name,started] of states){
    const old=progressSeen.get(key), tag=`${state}:${started??''}`;
    if(state==='done'&&key.startsWith('download:'))progressSeen.delete(key); else progressSeen.set(key,{state,name,tag});
    if(first||old?.tag===tag||(!old&&state!=='running'))continue;
    announce({running:t('{job} started',{job:name}),done:t('{job} finished',{job:name}),error:t('{job} failed',{job:name}),cancelled:t('{job} cancelled',{job:name})}[state]);
  }
  if(first)progressSeen.set('',{});
}
const ago=seconds=>seconds<60?`${Math.max(1,Math.round(seconds))} s`:seconds<3600?`${Math.round(seconds/60)} min`:`${Math.round(seconds/3600)} h`;
function renderInference(s) {
  const rows=(s.models||[]).filter(answering).map(m=>{
    const h=m.health||{}, inflight=m.inflight||[], bySource={};
    inflight.forEach(x=>{bySource[x.source]=(bySource[x.source]||0)+1;});
    // basal-serve's counters are read once a second: zeros next to requests in flight would only be out of date
    const counts=h.running>0||h.queued>0?t('{running} running · {queued} queued',{running:h.running??0,queued:h.queued??0}):tp(inflight.length,'{n} request','{n} requests');
    const sources=Object.entries(bySource).map(([k,n])=>`${t(INFERENCE_SOURCES[k]||k)} ${n}`).join(' · ');
    const longest=inflight.length?t('longest {seconds} s',{seconds:inflight[0].seconds.toFixed(1)}):'';
    return `<div class="inference-row"><span class="status-dot ready active"></span><strong>${esc(m.id)}</strong><span class="muted">${esc([counts,sources,longest].filter(Boolean).join(' · '))}</span></div>`;
  });
  for(const [kind,j] of Object.entries(s.jobs||{})){
    if(!j.running||!INFERENCE_JOBS[kind])continue;
    const share=j.total?Math.min(100,j.done/j.total*100):null;
    rows.push(`<div class="inference-row"><span class="status-dot ready active"></span><strong>${esc(t(INFERENCE_JOBS[kind]))}</strong><span class="muted">${esc([j.model,ts(j.phase,j.phase_code,j.phase_params),j.total?`${j.done} / ${j.total}`:''].filter(Boolean).join(' · '))}</span>${share==null?'':progressBar(j.done,j.total,t(INFERENCE_JOBS[kind]),`${j.done} / ${j.total}`)}</div>`);
  }
  const last=Math.max(0,...Object.values(statsData.models||{}).map(x=>Number(x.last_used)||0));
  const idle=last?t('Idle · last decision {ago} ago',{ago:ago(Date.now()/1000-last)}):t('Idle');
  $('#inferenceNow').innerHTML=rows.length?rows.join(''):`<div class="inference-row idle"><span class="status-dot"></span><span class="muted">${esc(idle)}</span></div>`;
}
function renderLoadedRows(models) {
  const markup=models.length?models.map(m=>{
    const stats=statsData.models?.[m.id], p=m.progress;
    const progress=p?`<div class="progress-wrap">${progressBar(p.done_bytes,p.total_bytes,t('Download of {model}',{model:shortName(m.id)}),`${gb(p.done_bytes)} / ${gb(p.total_bytes)}`)}<small aria-hidden="true">${gb(p.done_bytes)} / ${gb(p.total_bytes)}</small></div>`:'';
    const variant=modelData.find(x=>x.id===m.id), chips=`${m.mode?`<span class="chip">${esc(m.mode)}</span>`:''}${variant?.community?`<span class="chip">${t('community')}</span>`:''}${(m.media||[]).map(x=>`<span class="chip media">${esc(x)}</span>`).join('')}${['prompt_format','layout','orders','vllm_preset','adapter'].filter(k=>m.entry?.[k]!=null).map(k=>`<span class="chip">${esc(k)} ${esc(m.entry[k])}</span>`).join('')}${uncalibrated(m.entry)?`<span class="badge fit-tight" title="${t('temperature_per_prim is {}: the probabilities are the raw model outputs')}">${t('uncalibrated')}</span>`:''}${m.restart_required?`<span class="badge fit-tight">${t('Restart to apply')}</span>`:''}`;
    const health=m.health, load=health?.reachable===false?t('unreachable'):health?.reachable!==true?t('activity unavailable'):health.running==null||health.queued==null?t('load counters unavailable'):health.running===0&&health.queued===0?t('idle'):`${health.running} ${t('running')} · ${health.queued} ${t('queued')}`;
    const warning=m.entry?.calibration_warning?`<div class="reason warning wide">${esc(m.entry.calibration_warning)}</div>`:'';
    const actions=`<button type="button" data-restart="${esc(m.id)}" aria-label="${esc(t('Restart {model}',{model:shortName(m.id)}))}"${['ready','error'].includes(m.state)?'':' disabled'}>${t('Restart')}</button>${['ready','error'].includes(m.state)?optionsButton(m.id,m.media_requested,'restart'):''}<a class="btn" data-variant="outline" href="#logs/${esc(encodeURIComponent(m.id))}" aria-label="${esc(t('Logs of {model}',{model:shortName(m.id)}))}">${t('Logs')}</a><button type="button" data-unload="${esc(m.id)}" aria-label="${esc(t('Unload {model}',{model:shortName(m.id)}))}">${t('Unload')}</button>`;
    const optionChips=Object.entries(m.options||{}).filter(([k])=>k!=='mode').map(([k,v])=>`<span class="chip" title="${t('load option')}">${esc(v===true?k:v===false?t('no {option}',{option:k}):`${k} ${Array.isArray(v)?v.join('–'):v}`)}</span>`).join('');
    return `<div class="model-row"><div class="model-row-main"><span class="status-dot${m.state==='ready'?' ready':m.state==='error'?' error':''}${answering(m)?' active':''}"></span><span class="state-pill">${esc(t(m.state))}</span><strong>${esc(m.id)}</strong>${chips}${optionChips}<button type="button" class="model-card-open" data-id="${esc(m.id)}" aria-label="${esc(t('Information about {model}',{model:shortName(m.id)}))}"><span aria-hidden="true">ⓘ</span></button><span class="chip model-load">${esc(load)}</span></div><div class="muted model-row-meta">${m.rss_bytes==null?t('{estimated} estimated',{estimated:m.est_memory_bytes==null?t('unknown'):gb(m.est_memory_bytes)}):t('{estimated} estimated → {resident} resident',{estimated:m.est_memory_bytes==null?t('unknown'):gb(m.est_memory_bytes),resident:gb(m.rss_bytes)})}</div>${modelStatTiles(stats)}${progress}${warning}${m.error?`<div class="reason">${esc(m.error)}</div>`:''}<div class="model-row-actions">${actions}</div></div>`;
  }).join(''):`<div class="empty-state"><strong>${t('No model loaded')}</strong><span>${t('Load a model to decide in the Playground and serve it on the API.')}</span><div class="controls"><a class="btn primary" href="#library">${t('Load a model')}</a><a class="btn" href="#discover">${t('Discover models')}</a></div></div>`;
  // the poll runs every second on the Overview: an unchanged list keeps its nodes, and the focus on its buttons
  if(!renderKeepingFocus($('#loadedModels'),markup))return;
  $('#loadedModels').querySelectorAll('[data-restart]').forEach(b=>b.onclick=()=>act(()=>restartOne(b.dataset.restart)));
  $('#loadedModels').querySelectorAll('[data-unload]').forEach(b=>b.onclick=()=>act(()=>unloadOne(b.dataset.unload)));
}
function renderPlaygroundModels(ready) {
  const select=$('#playgroundModel'), signature=JSON.stringify(ready.map(m=>[m.id,m.media]));
  if(signature!==playgroundSignature){
    playgroundSignature=signature;
    select.innerHTML=ready.map(m=>`<option value="${esc(m.id)}">${esc(m.id)}</option>`).join('')||`<option value="">${t('No model loaded')}</option>`;
  }
  select.value=selectedModel; select.disabled=!ready.length;
  $('#playgroundForm').disabled=!ready.length; $('#playgroundHint').hidden=Boolean(ready.length);
  setPlaygroundMedia(ready.find(m=>m.id===selectedModel)?.media||[]);
  setPlaygroundExperimental(ready.find(m=>m.id===selectedModel));
  renderCompareModels(ready);
}
function setPlaygroundMedia(kinds) {
  if(JSON.stringify(kinds)===JSON.stringify(loadedMedia))return;
  loadedMedia=kinds; clearSelectedMedia(); syncRequest();
  $('#mediaInput').accept=[kinds.includes('image')?'image/*':'',kinds.includes('audio')?'audio/*':''].filter(Boolean).join(',');
}
$('#playgroundModel').addEventListener('change',e=>{selectedModel=e.target.value;localStorage.setItem('basal-ui.model',selectedModel);const model=readyModels().find(m=>m.id===selectedModel);setPlaygroundMedia(model?.media||[]);setPlaygroundExperimental(model);syncRequest(false,REQUEST_OPTION_KEYS);if(serveData)renderConnect();});
async function unloadOne(id){await api('/api/unload',{method:'POST',body:JSON.stringify({id})});await refreshStatus();}
async function restartOne(id){await api('/api/restart',{method:'POST',body:JSON.stringify({id})});toast(`${t('Restarting {model}',{model:shortName(id)})}`);await refreshStatus();}
async function unloadAll() {
  if(!await confirmDialog({title:t('Unload all models?'),description:t('Every loaded model stops; clients of the API server get 404 until a model is loaded again.'),confirmLabel:t('Unload all'),destructive:true}))return;
  await api('/api/unload',{method:'POST',body:'{}'}); await refreshStatus();
}
$('#footerUnload').onclick=()=>act(unloadAll);
