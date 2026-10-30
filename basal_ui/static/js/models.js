// Overview hardware, Discover (suggestions and the catalog), model cards, the Library's models, updates, loading.
'use strict';
function renderHardware(data) {
  hw = data;
  $('#deviceName').textContent = data.device_name || t('Unknown device');
  $('#platform').textContent = data.platform || '—';
  $('#hardwareBudget').textContent = gb(data.memory_bytes);
  $('#availableMemory').textContent = gb(data.available_bytes);
  $('#systemRam').textContent = gb(data.system_ram_bytes);
  $('#engines').innerHTML = (data.engines || []).map(x => `<span class="chip">${esc(x)}</span>`).join('') || `<span class="muted">${t('No engines detected')}</span>`;
}
function familyOrder(family) {
  return ({'basal-2.0':6,'basal-omni-1.1':5,'basal-omni-1.0':4,'basal-omni':3,'basal-1.5':2,'basal-1.0':1})[family] || 0;
}
function tierOrder(tier) { return ({pico:0,mini:1,'1.5B':2,'4.5B':3,max:4,ultra:5})[tier] ?? 9; }
let backendModelFilter=null;
function renderModels(data) {
  modelData = data.models || [];
  renderDiskModels(); renderCalibrationModels(); renderInspectModels();
  const suggestions = data.suggestions || [];
  renderSuggestions(suggestions);
  const overviewSuggestion=$('#overviewSuggested'), firstSuggestion=$('#suggested .suggestion-card');
  const overviewCard=firstSuggestion?.cloneNode(true), cardTitle=overviewCard?.querySelector('h2');
  if(cardTitle){const h3=document.createElement('h3');h3.innerHTML=cardTitle.innerHTML;cardTitle.replaceWith(h3);}  // under the card's own h2
  overviewSuggestion.replaceChildren(overviewCard||Object.assign(document.createElement('p'),{className:'muted',textContent:t('No suggested model fits right now.')}));
  const slots = new Map(suggestions.map(s => [s.id, s.slot]));
  const older = $('#olderToggle').checked, showBad = $('#notRunnableToggle').checked, downloadedOnly = $('#downloadedToggle').checked;
  const list = modelData.filter(m => (!backendModelFilter||backendModelFilter.ids.has(m.id)) && (older || !['basal-1.0','basal-1.5'].includes(m.family)) && (showBad || m.runnable) && (!downloadedOnly || m.downloaded));
  list.sort((a,b) => familyOrder(b.family)-familyOrder(a.family) || tierOrder(a.tier)-tierOrder(b.tier) || a.port.localeCompare(b.port));
  const groups = new Map(); for (const m of list) { if (!groups.has(m.family)) groups.set(m.family, []); groups.get(m.family).push(m); }
  if (!list.length) { $('#modelsTable').innerHTML = `<div class="empty">${t('No models match these filters.')}</div>`; return; }
  let html = '';
  const tableHead=`<table class="model-variants"><colgroup><col style="width:20%"><col style="width:13%"><col style="width:9%"><col style="width:11%"><col style="width:8%"><col style="width:9%"><col style="width:30%"></colgroup><thead><tr><th>${t('Tier')}</th><th>${t('Format')}</th><th>${t('Size')}</th><th>${t('Est. memory')}</th><th>${t('Fit')}</th><th>${t('Fit now')}</th><th>${t('Action')}</th></tr></thead><tbody>`;
  for (const [family, rows] of groups) {
    html += `<section class="model-family-group"><div class="disk-group-heading"><h2>${esc(family)}</h2><span class="muted">${tp(rows.length,'{n} variant','{n} variants',{n:rows.length})}</span></div><div class="table-wrap">${tableHead}`;
    for (const m of rows) {
      const classes = `fit-${esc(m.fit || 'unknown')}`;
      const reasonText = ts(m.reason, m.reason_code, m.reason_params);
      const reason = !m.runnable && m.reason ? `<span class="reason warning" title="${esc(reasonText)}">${esc(reasonText)}</span>` : '';
      const slot = slots.get(m.id);
      const mediaReasonText = ts(m.media_reason, m.media_reason_code, m.media_reason_params);
      const mediaReason = !m.media_mode && (m.media || []).length ? `<small class="muted">${esc(mediaReasonText || '')}</small>` : '';
      const actions=lifecycleActions(m,'discover');
      html += `<tr class="${m.runnable?'':'not-runnable'}"><td class="model-tier"><span class="model-name">${esc(m.tier)}</span>${reason}${mediaReason}</td><td class="model-format">${esc(m.label)} ${m.community?`<span class="chip">${t('community')}</span>`:''}${m.revision?`<span class="chip">${t('revision {revision}',{revision:esc(m.revision)})}</span>`:''}${m.downloaded?`<small class="model-downloaded muted">${t('On disk')}${m.downloaded_bytes==null?'':` · ${gb(m.downloaded_bytes)}`}</small>`:''}</td><td class="model-size">${gb(m.weights_bytes)}</td><td class="model-size">${gb(m.est_memory_bytes)}</td><td><span class="badge ${classes}">${esc(t(m.fit || 'unknown'))}</span></td><td>${fitMeter(m,true)}</td><td><div class="model-actions">${actions}${detailsButton(m)}</div></td></tr>`;
    }
    html += '</tbody></table></div></section>';
  }
  $('#modelsTable').innerHTML = html;
}
const deleteButton=id=>`<button type="button" class="delete-model destructive" data-id="${esc(id)}">${t('Delete')}</button>`;
// the model buttons of every list (Models, Model manager, the Overview's copy of a suggestion card), bound once
document.addEventListener('click',e=>{
  const b=e.target.closest('.download-model,.unload-model,.delete-model,.load-model,.use-model,.copy-endpoint');
  if(!b||b.disabled)return;
  const id=b.dataset.id;
  if(b.classList.contains('use-model'))useInPlayground(id);
  else if(b.classList.contains('copy-endpoint'))act(()=>copyEndpoint());
  else if(b.classList.contains('download-model'))act(async()=>{await api('/api/download',{method:'POST',body:JSON.stringify({id})});await refreshStatus();});
  else if(b.classList.contains('unload-model'))act(()=>unloadOne(id));
  else if(b.classList.contains('delete-model'))act(()=>deleteModel(id));
  else loadModel(id,b.dataset.media==='true');
});
function useInPlayground(id) {
  selectedModel=id; store('basal-ui.model',id);
  renderPlaygroundModels(readyModels()); $('#playgroundModel').dispatchEvent(new Event('change'));
  showView('playground',true);
}
// a model's next step wherever it is listed: Download & load (or Download when it does not fit now) → Load →
// Use in Playground / Copy endpoint / Restart options / Unload; "library" adds Load with media and Load with options,
// "discover" the setup of the next load (Load options saved without loading)
const loadedEntry=id=>statusCache?.models?.find(x=>x.id===id);
function lifecycleActions(m, place) {
  const entry=loadedEntry(m.id), download=statusCache?.downloads?.[m.repo], library=place==='library';
  const unload=`<button type="button" class="unload-model" data-id="${esc(m.id)}">${t('Unload')}</button>`;
  if(entry?.state==='ready')return `<span class="chip">${t('loaded')}</span><button type="button" class="use-model primary" data-id="${esc(m.id)}">${t('Use in Playground')}</button><button type="button" class="copy-endpoint" data-id="${esc(m.id)}">${t('Copy endpoint')}</button>${optionsButton(m.id,entry.media_requested,'restart')}${unload}`;
  if(entry){
    const p=entry.progress, progress=p?progressBar(p.done_bytes,p.total_bytes,t('Download of {model}',{model:shortName(m.id)}),`${gb(p.done_bytes)} / ${gb(p.total_bytes)}`):'';
    return `<span class="chip">${esc(t(entry.state))}</span>${progress}${entry.state==='error'&&entry.error?`<span class="reason">${esc(entry.error)}</span>`:''}${unload}`;
  }
  if(download&&!download.error)return downloadControl(m,download);
  if(!m.runnable){const reason=ts(m.reason,m.reason_code,m.reason_params)||t('not runnable here');return library?`<span class="reason warning" title="${esc(reason)}">${esc(reason)}</span>${deleteButton(m.id)}`:m.downloaded?deleteButton(m.id):'';}
  if(m.downloaded){
    const media=library&&m.media_mode?`<button type="button" class="load-model load-media-model" data-id="${esc(m.id)}" data-media="true" title="${esc(mediaTitle(m.media||[]))}">${t('Load with media')}</button>`:'';
    const noMediaText=ts(m.media_reason,m.media_reason_code,m.media_reason_params);
    const noMedia=library&&!m.media_mode&&(m.media||[]).length?`<small class="muted">${esc(noMediaText||'')}</small>`:'';
    return `<button type="button" class="load-model primary" data-id="${esc(m.id)}">${t('Load')}</button>${media}${library?optionsButton(m.id):optionsButton(m.id,false,'save')}${deleteButton(m.id)}${noMedia}`;
  }
  const fits=fitNow(m)!=='too_large';
  return `${fits?`<button type="button" class="load-model primary" data-id="${esc(m.id)}">${t('Download & load')}</button>`:''}<button type="button" class="download-model" data-id="${esc(m.id)}">${fits?t('Download only'):t('Download')}</button>${optionsButton(m.id,false,'save')}${download?.error?`<span class="reason">${esc(download.error)}</span>`:''}`;
}
const detailsButton=m=>`<button type="button" class="model-card-open details-button" data-id="${esc(m.id)}" aria-label="${esc(t('Details of {model}',{model:`${m.family} ${m.tier} ${m.label}`}))}">${t('Details')}</button>`;
// fit now, as the catalog decides it (catalog._fit): the estimate within 85% of the memory available now fits, within
// 100% is tight; the available memory comes from the latest status poll. Without a reading: the memory budget
const availableNow=()=>Number(statusCache?.memory?.available_bytes??hw?.available_bytes)||null;
const memoryBudget=()=>Number(statusCache?.memory?.budget_bytes??hw?.memory_bytes)||null;
function fitNow(m) {
  const est=Number(m.est_memory_bytes), available=availableNow();
  if(!est||!available)return m.fit_now&&m.fit_now!=='unknown'?m.fit_now:m.fit||'unknown';
  return est<=0.85*available?'fits':est<=available?'tight':'too_large';
}
const FIT_VERDICT={fits:'Fits comfortably',tight:'Fits, but tight',too_large:'Too large for the memory available now',unknown:'Fit unknown'};
function fitMeter(m, compact=false) {
  const fit=fitNow(m), verdict=t(FIT_VERDICT[fit]||FIT_VERDICT.unknown), est=Number(m.est_memory_bytes), now=availableNow(), basis=now||memoryBudget();
  if(!est||!basis)return html`<span class="badge fit-${fit}">${verdict}</span>`.text;
  const share=Math.min(100,est/basis*100), weights=Number(m.weights_bytes)||0, overhead=Math.max(0,est-Math.round(weights*1.2));
  const vars={weights:gb(weights),overhead:gb(overhead),estimate:gb(est),available:gb(basis)};
  const sentence=now?t('est. {estimate} of {available} available',vars):t('est. {estimate} of the {available} memory budget',vars);
  const math=now?t('Weights {weights} × 1.2 + {overhead} runtime = {estimate}. It fits comfortably up to 85% of the {available} available now and is tight up to 100%.',vars)
    :t('Weights {weights} × 1.2 + {overhead} runtime = {estimate}. The memory available now is unknown, so it is compared with the {available} memory budget: comfortable up to 85%, tight up to 100%.',vars);
  const meter=html`<div class="meter${fit==='too_large'?' danger':fit==='tight'?' warning':''}" role="meter" aria-label="${t('Estimated memory')}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(share)}" aria-valuetext="${verdict}: ${sentence}"><span class="meter-fill" style="width:${share.toFixed(1)}%"></span><span class="meter-mark" style="left:85%"></span></div>`;
  if(compact)return html`<div class="fit-meter compact" title="${math}"><span class="badge fit-${fit}">${verdict}</span>${meter}<small class="muted">${sentence}</small></div>`.text;
  return html`<div class="fit-meter"><div class="fit-meter-head"><span class="badge fit-${fit}">${verdict}</span><span class="fit-meter-text">${sentence}</span></div>${meter}<details class="fit-math"><summary>${t('How is this estimated?')}</summary><p>${math}</p></details></div>`.text;
}
async function deleteModel(id) {
  const model=modelData.find(m=>m.id===id);
  if(!model||!await confirmDialog({title:t('Delete model from disk?'),description:t('{model} will be removed from disk.',{model:`${model.family} ${model.tier} (${model.label})`}),confirmLabel:t('Delete'),destructive:true}))return;
  await api('/api/models/delete',{method:'POST',body:JSON.stringify({id})}); toast(t('Model deleted')); await loadModels();
}
// "basal-2.0" / "basal-omni-1.0" written the way basal.si5.pl writes the family names
function familyMark(family) {
  const parts = String(family).match(/^basal-(omni-)?(.+)$/);
  if (!parts) return esc(family);
  return parts[1] ? `basal<span class="family-suffix">-omni</span><small>${esc(parts[2])}</small>` : `basal<span class="family-suffix">-${esc(parts[2])}</span>`;
}
function downloadControl(m, progress) {
  if(progress&&!progress.error)return `<button class="download-model" data-id="${esc(m.id)}" disabled>${t('Downloading…')}</button><small class="muted download-progress" role="progressbar" aria-label="${esc(t('Download of {model}',{model:shortName(m.id)}))}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${progress.total_bytes?Math.round(Math.min(100,progress.done_bytes/progress.total_bytes*100)):0}" aria-valuetext="${gb(progress.done_bytes)} / ${gb(progress.total_bytes)}">${gb(progress.done_bytes)} / ${gb(progress.total_bytes)}</small>`;
  return `<button class="download-model" data-id="${esc(m.id)}">${t('Download')}</button>${progress?.error?`<span class="reason">${esc(progress.error)}</span>`:''}`;
}
function renderSuggestions(suggestions) {
  if (!suggestions.length) { $('#suggested').innerHTML = `<div class="empty">${t('Nothing fits the memory available now; free memory or use All models')}</div>`; return; }
  const models = new Map(modelData.map(m => [m.id, m]));
  $('#suggested').innerHTML = `<div class="suggestion-grid">${suggestions.map(s => {
    const m = models.get(s.id);
    if (!m) return '';
    const why = esc(ts(s.why, s.why_code, s.why_params)).replace(/(--[a-z][\w-]*(?: [a-z][\w.-]*)?)/g, '<code>$1</code>');
    const action=`<div class="controls suggestion-actions">${lifecycleActions(m,'discover')}</div>`;
    return `<article class="suggestion-card${m.family==='omni'?' omni':''}"><div class="eyebrow">${esc(t(s.slot.replace('_',' ')))}</div><h2>${familyMark(m.family)}</h2><div class="suggestion-tier">${esc(m.tier)} · ${esc(m.label)} ${m.community?`<span class="chip">${t('community')}</span>`:''}${m.revision?`<span class="chip">${t('revision {revision}',{revision:esc(m.revision)})}</span>`:''}${detailsButton(m)}</div>${fitMeter(m)}<p class="why">${why}</p>${m.downloaded?`<div class="suggestion-downloaded">✓ ${t('On disk')} · ${gb(m.downloaded_bytes)}</div>`:''}${action}</article>`;
  }).join('')}</div>`;
}
document.addEventListener('click',e=>{const b=e.target.closest('.model-card-open');if(b)act(()=>busyWhile(b,()=>openModelCard(b.dataset.id)));});
async function openModelCard(id) {
  const c=await api(`/api/models/card?id=${encodeURIComponent(id)}`);
  const scheme=c.quantisation?.scheme, sameScheme=[c.port,c.label].some(x=>String(x||'').toLowerCase()===String(scheme||'').toLowerCase());
  // family, tier and format are the title; the quantisation only where it says more than the format
  const assessmentReason=ts(c.assessment?.reason,c.assessment?.reason_code,c.assessment?.reason_params);
  const facts=[['Quantisation',sameScheme?null:scheme],['Size',gb(c.size_bytes)],['Media',(c.media||[]).join(', ')||t('none')],['Mode',`${c.assessment?.mode||'—'} · ${c.assessment?.runnable?t('runnable'):assessmentReason||t('not runnable')}`],['Serving',c.quantisation?.engine],['Fit',c.assessment?.fit],['Fit now',c.assessment?.fit_now],['Estimated memory',gb(c.assessment?.est_memory_bytes)],['Downloaded',c.downloaded?t('yes'):t('no')],['Local directory',c.local_dir],['Cached revisions',(c.revisions||[]).join(', ')]];
  const entry=c.loaded, shipped=c.shipped_calibration;
  $('#modelCardTitle').textContent=[c.family,c.tier,c.label].filter(Boolean).join(' · ')||c.id;
  $('#modelCardRepo').innerHTML=`<a href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.repo)}</a> · ${esc(c.author||'')}${c.community?` <span class="chip">${t('community')}</span>`:''}${c.base_repo?` · ${t('base')} ${esc(c.base_repo)}${c.base_revision?` @ ${esc(c.base_revision)}`:''}`:''}`;
  const shown=facts.filter(([,v])=>v!=null&&v!==''&&v!=='—').map(([k,v])=>[k,k==='Fit'||k==='Fit now'?t(v):v]), wide=new Set(['Local directory','Cached revisions']);  // compact: facts this checkpoint does not have are left out; paths and hashes take a whole row
  $('#modelCardBody').innerHTML=`<dl class="model-card-facts">${shown.map(([k,v])=>`<dt${wide.has(k)?' class="wide"':''}>${esc(t(k))}</dt><dd${wide.has(k)?' class="wide"':''}>${esc(v)}</dd>`).join('')}</dl>${c.quantisation?.description?`<h3>${t('Quantisation')}</h3><p>${esc(c.quantisation.description)}</p>`:''}<h3>${t('Shipped calibration')}</h3>${shipped?.error?`<p class="reason">${esc(shipped.error)}</p>`:shipped?`${shipped.fitted_on&&typeof shipped.fitted_on==='object'?`<p>${t('Fitted on {date} · {decisions} decisions',{date:esc(shipped.fitted_on.data||'—'),decisions:esc(shipped.fitted_on.decisions??'—')})}</p>`:''}<div class="chips">${thresholdCell(shipped.thresholds)}</div>`:`<p class="muted">${t('None')}</p>`}<details><summary>${t('Prompt format')}</summary><pre>${esc(JSON.stringify(c.basal_json?.prompt_format??c.basal_json??null,null,2))}</pre></details>${entry?`<details><summary>${t('Loaded entry')}</summary><pre>${esc(JSON.stringify(Object.fromEntries(['mode','orders','prompt_format','layout','vllm_preset','certified','context'].filter(k=>entry[k]!=null).map(k=>[k,entry[k]])),null,2))}</pre></details>`:''}${c.readme?`<details><summary>${t('README')}</summary><pre>${esc(c.readme)}</pre></details>`:''}`;  // the long JSON / README blocks start collapsed
  $('#modelCardDialog').showModal();
  $('#modelCardBody').scrollTop=0;  // the dialog is reused: a new card starts at its top
}
function renderDiskModels() {
  const onDisk=modelData.filter(m=>m.downloaded), total=onDisk.reduce((sum,m)=>sum+(Number(m.downloaded_bytes)||0),0);
  const rows=modelData.filter(m=>m.downloaded||loadedEntry(m.id)||statusCache?.downloads?.[m.repo]);  // and the ones on their way
  const groups=new Map();
  for(const model of rows){
    const type=model.family||'Other';
    if(!groups.has(type))groups.set(type,[]);
    groups.get(type).push(model);
  }
  $('#diskModels').innerHTML=rows.length?[...groups].sort(([a],[b])=>familyOrder(b)-familyOrder(a)||a.localeCompare(b)).map(([type,models])=>{
    const bytes=models.reduce((sum,m)=>sum+(Number(m.downloaded_bytes)||0),0);
    return `<section class="disk-group"><div class="disk-group-heading"><h3>${esc(t(type))}</h3><span class="muted">${tp(models.length,'{n} model','{n} models',{n:models.length})} · ${gb(bytes)}</span></div>${models.map(m => `<div class="disk-row${loadedEntry(m.id)?.state==='ready'?' is-loaded':''}"><div class="disk-info"><strong>${esc(m.tier)} · ${esc(m.label)}</strong>${m.community?`<span class="chip">${t('community')}</span>`:''}${m.downloaded?`<span class="muted">${gb(m.downloaded_bytes)}</span>`:statusCache?.downloads?.[m.repo]?`<span class="muted">${t('downloading')}</span>`:''}${detailsButton(m)}</div><div class="disk-actions">${diskActions(m)}</div></div>`).join('')}</section>`;  // the family is the group's heading
  }).join(''):`<div class="empty-state"><strong>${t('Nothing downloaded yet')}</strong><span>${t('Discover lists the models that fit this machine.')}</span><div class="controls"><a class="btn primary" href="#discover">${t('Discover models')}</a></div></div>`;
  $('#diskTotal').hidden=!onDisk.length;
  $('#diskTotal').textContent = t('Total: {size} in {models}',{size:gb(total),models:tp(onDisk.length,'{n} model','{n} models',{n:onDisk.length})});
  $('#diskSummary').textContent=t('{models} on disk',{models:tp(onDisk.length,'{n} model','{n} models',{n:onDisk.length})});
  $('#diskSummaryMeta').textContent=t('{size} total',{size:gb(total)});
}
// Library: load (text or media, or with options), use, copy the endpoint, unload and delete the models on disk
const diskActions=m=>lifecycleActions(m,'library');
async function loadSettings() {
  try {
    const settings = await api('/api/settings');
    $('#modelsDir').value = settings.models_dir; $('#modelsDir').dispatchEvent(new Event('pathcheck'));
    $('#defaultModelsDirHint').textContent = t('Default: {path}',{path:settings.default_models_dir});
    $('#libraryDir').textContent = t('Models directory: {path}',{path:settings.models_dir});
    setChecked($('#checkUpdates'),settings.check_updates!==false);
  } catch (e) { $('#modelsDirError').textContent = e.message; }
}
async function refreshUpdates() {
  try {
    const d=await api('/api/updates');
    const parts=[];
    if(d.newer)parts.push(`<div class="card"><button type="button" data-dismiss-update aria-label="${t('Dismiss')}">×</button><strong>${t('basal-ui {latest} is available — you have {installed}',{latest:esc(d.latest),installed:esc(d.installed)})}</strong> <a href="${esc(d.url)}" target="_blank" rel="noopener">${t('Release notes')}</a></div>`);
    const engine=d.engine||{};
    if(engine.newer)parts.push(`<div class="card"><button type="button" data-dismiss-update aria-label="${t('Dismiss')}">×</button><strong>${engine.latest_supported?t('basal engine {latest} is available — you have {installed}',{latest:esc(engine.latest),installed:esc(engine.version)}):t('basal engine {latest} is released; this basal-ui supports {supported}',{latest:esc(engine.latest),supported:esc(engine.supported)})}</strong> <a href="${esc(engine.url)}" target="_blank" rel="noopener">${t('Release notes')}</a></div>`);
    const badge=$('#engineBadge');
    badge.hidden=!engine.experimental;
    if(engine.experimental){badge.textContent=t('experimental engine {version}',{version:`${engine.version}${engine.commit?'@'+engine.commit.slice(0,7):''}`});badge.title=t('basal-ui supports engine {supported}',{supported:engine.supported});}
    if(d.new_models?.length)parts.push(`<div class="card"><button type="button" data-dismiss-update aria-label="${t('Dismiss')}">×</button><strong>${tp(d.new_models.length,'{n} new model','{n} new models',{n:d.new_models.length})} ${t('in the catalog')}</strong> <button type="button" id="showNewModels">${t('View models')}</button></div>`);
    $('#updatesBanner').innerHTML=parts.join('');
    const b=$('#showNewModels');if(b)b.onclick=()=>act(async()=>{await api('/api/updates/seen',{method:'POST'});await refreshUpdates();showView('models',true);selectModelsTab('all');});
  } catch {}
}
$('#checkUpdates').onchange=()=>act(async()=>{await api('/api/settings',{method:'PUT',body:JSON.stringify({check_updates:$('#checkUpdates').checked})});refreshUpdates();});
async function saveModelsDir(value) {
  $('#modelsDirError').textContent = '';
  try {
    await api('/api/settings', {method:'PUT', body:JSON.stringify({models_dir:value})});
    await Promise.all([loadSettings(),loadModels()]); toast(t('Models directory saved'));
  } catch (e) {  // one inline alert; the 409 (models still loaded) in the UI language
    $('#modelsDirError').textContent = e.message.includes('unload every model')?t('Unload every model before changing the models directory.'):e.message;
  }
}
// the model lists re-rendered under the keyboard focus (Load, Unload or Download pressed; a status poll) keep it
function keepingModelFocus(fn) {
  const restores=['#suggested','#modelsTable','#diskModels','#overviewSuggested'].map(s=>keepFocus($(s)));
  const done=()=>restores.forEach(restore=>restore()), result=fn();
  return result instanceof Promise?result.finally(done):(done(),result);
}
const loadModels=(refresh=false)=>keepingModelFocus(()=>fetchModels(refresh));
async function fetchModels(refresh) {
  $('#modelsTable').innerHTML = '<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>';
  $('#suggested').innerHTML = `<div class="empty">${t('Loading suggestions…')}</div>`;
  try { renderModels(await api(`/api/models${refresh?'?refresh=1':''}`)); }
  catch (e) { $('#modelsTable').innerHTML = `<div class="error-box">${esc(e.message)}</div>`; $('#suggested').innerHTML = `<div class="error-box">${esc(e.message)}</div>`; toast(e.message,'error'); }
}
async function loadModel(id, media=false) {
  statusSequence++;
  clearSelectedMedia();
  try { await api('/api/load', {method:'POST', body:JSON.stringify({id,...(media?{media:true}:{})})}); toast(t('Model load started')); await refreshStatus(); }
  catch (e) { toast(e.message,'error'); }
}
// the status poll's one error toast (key 'status'): replaced while the server stays down, dismissed once it answers
function showStatusError(text) { if(text)toast(text,'error',{key:'status'}); }
$('#refreshModels').addEventListener('click',()=>loadModels(true));
$('#downloadedToggle').addEventListener('change',rerenderFilters);
$('#saveModelsDir').addEventListener('click',()=>saveModelsDir($('#modelsDir').value));
$('#defaultModelsDir').addEventListener('click',()=>saveModelsDir(null));
let catalogData=null;
const originalRenderModels=renderModels;
renderModels=function(data){catalogData=data; originalRenderModels(data);};
function rerenderFilters(){if(catalogData)keepingModelFocus(()=>originalRenderModels(catalogData));}
$('#olderToggle').addEventListener('change',rerenderFilters);
$('#notRunnableToggle').addEventListener('change',rerenderFilters);
