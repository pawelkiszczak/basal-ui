// The Load options dialog.
'use strict';
// Load options: the engine mode and basal-serve's runtime settings of one load (basal_ui/options.py)
const GRAPH_MODES=['fast','fast-nocompile','fast-exit','fp8','nvfp4'];
const OPTION_SECTIONS=[
  ['Engine',['revision','adapter','ollama_url','ollama_model','llamacpp_url','http_parallel','quant','attn','dtype','vllm_preset','gpu_memory','vllm_fast_prefill','device','eager_attn','eager_forward','ple_offload','exit_heads','mlx_packed']],
  ['Determinism & orders',['deterministic','orders','auto_tau','auto_band']],
  ['Batching',['max_batch','wait_ms','batch_window_ms','soam']],
  ['Caching',['state_cache_gb','prefix_cache']],
  ['Scheduling',['scheduler','long_lane','long_tokens','long_share','long_slice_ms']],
  ['Context',['layout','context_overflow','truncate_head','max_len']],
  ['Kernels (CUDA graph engine)',['gemma_attn','merge','lean','tree','batches','readout_layer','template_prefix','fp8_accum']],
  ['Calibration',['strict_calibration']],
  ['Experimental',['think','think_tokens','think_python','think_url','think_calibration','think_model','think_adapter','think_memory','think_max_len','think_log','early_exit']],
];
const OPTION_FIELDS={
  mlx_packed:{label:'MLX packed (opt-in)',kind:'bool',modes:['mlx','mlx-q8']},
  quant:{label:'Quantisation',kind:'select',values:[[null,'as the mode'],['fp8','fp8'],['nvfp4','nvfp4'],['q8','q8 (mlx)']],modes:[...GRAPH_MODES,'mlx','mlx-q8']},
  attn:{label:'Attention kernel',kind:'select',values:['auto','fa2','fa3','fa4','cudnn'],modes:GRAPH_MODES},
  gemma_attn:{label:'Gemma 4 attention (graph engine)',kind:'select',values:['auto','math','flex','fa3','fa3+math'],modes:GRAPH_MODES},
  dtype:{label:'dtype',kind:'select',values:['bfloat16','float16','float32'],modes:[...GRAPH_MODES,'eager','mps','vllm']},
  vllm_fast_prefill:{label:'vLLM KV-sharing fast prefill (Gemma 4)',kind:'select',values:['auto','off'],modes:['vllm']},
  eager_attn:{label:'Gemma 4 eager attention',kind:'text',placeholder:'math, auto or sliding=K,full=K',modes:['eager']},
  eager_forward:{label:'Gemma 4 eager forward',kind:'select',values:[[null,'basal (default)'],['basal','basal'],['hf','hf (transformers)']],modes:['eager']},
  ple_offload:{label:'Per-layer embeddings in host memory (Gemma 4 E2B / E4B)',kind:'bool',modes:['eager']},
  exit_heads:{label:'Exit heads directory',kind:'text',placeholder:'<model>/exit_heads',modes:['fast-exit']},
  merge:{label:'Merged projections',kind:'select',values:['auto','on','off'],modes:GRAPH_MODES},
  lean:{label:'Lean layer function',kind:'select',values:['off','auto','on','pass2'],modes:GRAPH_MODES},
  tree:{label:'Pass-2 trie mask',kind:'select',values:['dense','auto','flex'],modes:GRAPH_MODES},
  batches:{label:'Batch rows',kind:'select',values:['auto','compact','default','fine'],modes:GRAPH_MODES},
  readout_layer:{label:'Readout-only last layer',kind:'select',values:['off','auto','on'],modes:GRAPH_MODES},
  template_prefix:{label:'Template prefix computed once',kind:'select',values:['auto','on','off'],modes:GRAPH_MODES},
  fp8_accum:{label:'FP8 accumulation',kind:'select',values:['fast','full'],modes:GRAPH_MODES},
  strict_calibration:{label:'Refuse a calibration fitted on another model (--strict-calibration)',kind:'bool'},
  vllm_preset:{label:'vLLM preset',kind:'select',values:['default','deterministic'],modes:['vllm']},
  gpu_memory:{label:'GPU memory share',kind:'num',attrs:'min="0.05" max="1" step="0.05"',modes:['vllm','sglang']},
  device:{label:'Device',kind:'select',values:[[null,'auto'],'cuda','mps','cpu'],modes:['eager','mps']},
  ollama_url:{label:'Ollama URL',kind:'text',modes:['ollama']},
  ollama_model:{label:'Ollama model',kind:'text',placeholder:'derived from the model',modes:['ollama']},
  llamacpp_url:{label:'llama-server URL',kind:'text',modes:['llamacpp']},
  http_parallel:{label:'Parallel HTTP requests',kind:'num',attrs:'min="1" step="1"',modes:['ollama','llamacpp']},
  deterministic:{label:'Deterministic (decisions independent of batching)',kind:'bool'},
  orders:{label:'Option orders',kind:'select',values:[[null,'model default'],'1','2','auto','2le4','2le8']},
  auto_tau:{label:'auto: margin τ',kind:'num',attrs:'min="0" max="1" step="0.05"'},
  auto_band:{label:'auto: band (low, high)',kind:'band'},
  max_batch:{label:'Max batch',kind:'num',attrs:'min="1" step="1"'},
  wait_ms:{label:'Wait (ms)',kind:'num',attrs:'min="0" step="0.5"'},
  batch_window_ms:{label:'Batch window (ms)',kind:'num',attrs:'min="0" step="0.5"'},
  soam:{label:'SOAM (shared option attention mask)',kind:'bool'},
  state_cache_gb:{label:'State cache (GB)',kind:'num',attrs:'min="0" step="0.5"'},
  prefix_cache:{label:'Prefix cache',kind:'bool'},
  scheduler:{label:'Scheduler',kind:'select',values:['auto','on','off']},
  long_lane:{label:'Long-request lane',kind:'bool'},
  long_tokens:{label:'Long request from (tokens)',kind:'num',attrs:'min="1" step="1"'},
  long_share:{label:'Long lane share',kind:'num',attrs:'min="0" max="1" step="0.05"'},
  long_slice_ms:{label:'Long lane slice (ms)',kind:'num',attrs:'min="0" step="1"'},
  layout:{label:'Prompt layout (prompt-format-2 models)',kind:'select',values:[[null,'model default (basal.json)'],['canonical','canonical'],['state_twice','state_twice (state read twice)'],['qfirst','qfirst (question first, one pass per question)']]},
  context_overflow:{label:'Prompt over the context',kind:'select',values:[['truncate','truncate the state'],['refuse','refuse (422)']]},
  truncate_head:{label:'Kept from the beginning',kind:'percent'},
  max_len:{label:'Max length (vLLM / SGLang)',kind:'num',attrs:'min="512" max="131072" step="512"',placeholder:'API server setting'},
  revision:{label:'Model revision',kind:'text',placeholder:'latest (main)'},
  adapter:{label:'LoRA adapter directory (merged at load)',kind:'text',placeholder:'/path/to/adapter'},
  think:{label:'Thinking (reason before low-margin decisions)',kind:'bool'},
  think_tokens:{label:'Reasoning tokens',kind:'num',attrs:'min="1" step="32"',think:true},
  think_python:{label:'Python with vLLM',kind:'text',placeholder:'basal-ui’s Python',think:true},
  think_url:{label:'Running think server URL',kind:'text',placeholder:'start one',think:true},
  think_calibration:{label:'Readout-2 calibration',kind:'text',placeholder:'<model>/CALIBRATION.think.json or off',think:true},
  think_model:{label:'Think weights',kind:'text',placeholder:'<model>/think/',think:true},
  think_adapter:{label:'LoRA adapter',kind:'text',placeholder:'<model>/think/adapter',think:true},
  think_memory:{label:'vLLM GPU memory fraction',kind:'num',attrs:'min="0.05" max="1" step="0.05"',think:true},
  think_max_len:{label:'vLLM context',kind:'num',attrs:'min="512" step="512"',think:true},
  think_log:{label:'vLLM log file',kind:'text',placeholder:'basal-think-vllm.log',think:true},
  early_exit:{label:'Early exit (default policy)',kind:'select',values:['off','0.999','0.995','0.99','0.98']},
};
// action: "load" (Model manager), "save" (Models: set up the options of the next load), "restart" (a loaded model)
// one label everywhere: "Load options" (before a load) or "Restart options" (a loaded model), named with its model
const optionsButton=(id,media=false,action='load')=>{
  // restart: a loaded model restarts with them; save: kept for its next load (nothing loads); load: loads with them
  const model=shortName(id), label=action==='restart'?t('Restart {model} with other load options',{model}):action==='save'?t('Set up the next load of {model}',{model}):t('Load options for {model}',{model});
  return `<button type="button" class="options-model" data-id="${esc(id)}" data-media="${media}" data-action="${action}" aria-label="${esc(label)}" title="${esc(label)}"><span aria-hidden="true">⚙</span> ${action==='restart'?t('Restart options'):action==='save'?t('Set up next load'):t('Load options')}</button>`;
};
document.body.insertAdjacentHTML('beforeend',`<dialog id="loadOptions" class="confirm-dialog options-dialog" aria-labelledby="loadOptionsTitle"><h2 id="loadOptionsTitle">${t('Load options')}</h2><p id="optionsModel" class="muted"></p><p id="optionsSetupNote" class="muted option-note" hidden>${t('Media loads choose their mode (eager or vLLM) when loading from the Library.')}</p><div id="optionsForm"></div><div id="optionsError" class="error-box" role="alert" hidden></div><div class="dialog-actions"><label><input type="checkbox" role="switch" id="optionsRemember"> ${t('Remember for this model')}</label><button type="button" id="optionsReset">${t('Reset to defaults')}</button><button type="button" id="optionsCancel">${t('Cancel')}</button><button type="button" id="optionsApply" class="primary">${t('Load')}</button></div></dialog>`);
$('#optionsModel').insertAdjacentHTML('afterend',`<div id="loadPresetControls" class="controls"><select id="loadPresetSelect" aria-label="${t('Load preset')}"></select><button type="button" id="applyLoadPreset">${t('Apply')}</button><button type="button" id="saveLoadPreset">${t('Save as…')}</button><button type="button" id="deleteLoadPreset">${t('Delete')}</button></div>`);
// Recommended (the defaults; the sections stay hidden) or Custom (every section, with what differs from the defaults)
$('#optionsModel').insertAdjacentHTML('afterend',`<div class="options-choice" role="radiogroup" aria-label="${t('Load settings')}"><label><input type="radio" name="optionsChoice" value="recommended"> ${t('Recommended')}</label><label><input type="radio" name="optionsChoice" value="custom"> ${t('Custom')}</label></div></div>`);
$('#optionsForm').insertAdjacentHTML('beforebegin','<p id="optionsRecommendedNote" class="callout info"></p><div id="optionsDiff" class="options-diff"></div>');
document.querySelectorAll('input[name=optionsChoice]').forEach(r=>r.onchange=()=>setOptionsCustom(r.value==='custom'));
$('#saveLoadPreset').onclick=()=>promptPresetName('load');
$('#applyLoadPreset').onclick=()=>{const v=presetData.load?.[$('#loadPresetSelect').value];if(v){renderOptionsForm({...optionsState.data.defaults,...v});renderOptionsDiff();}};
$('#deleteLoadPreset').onclick=()=>act(async()=>{const n=$('#loadPresetSelect').value;if(n)await deletePreset('load',n);});
$('#loadPresetSelect').onchange=()=>$('#deleteLoadPreset').disabled=!$('#loadPresetSelect').value;
let optionsState=null;
const optionValue=v=>v===null||v===undefined?'':String(v);
function optionInput(key, value) {
  const f=OPTION_FIELDS[key], id=`opt-${key}`;
  if(f.kind==='bool')return `<label><input type="checkbox" role="switch" id="${id}"${value?' checked aria-checked="true"':' aria-checked="false"'}> ${esc(t(f.label))}</label>`;
  const listed=(f.values||[]).map(v=>Array.isArray(v)?v:[v,v]);
  const input=f.kind==='select'?`<select id="${id}">${listed.map(([v,l])=>`<option value="${esc(optionValue(v))}"${optionValue(v)===optionValue(value)?' selected':''}>${esc(t(l))}</option>`).join('')}${listed.some(([v])=>optionValue(v)===optionValue(value))?'':`<option value="${esc(optionValue(value))}" selected>${esc(value)}</option>`}</select>`
    :f.kind==='band'?`<span class="controls"><input type="number" id="${id}-lo" min="0" max="1" step="0.05" value="${esc(value?.[0])}" aria-label="${esc(t(f.label))} ${t('low')}"><input type="number" id="${id}-hi" min="0" max="1" step="0.05" value="${esc(value?.[1])}" aria-label="${esc(t(f.label))} ${t('high')}"></span>`
    :f.kind==='percent'?`<span class="controls"><input type="range" id="${id}" min="0" max="100" step="1" value="${Math.round(Number(value)*100)}"><output id="${id}-out">${Math.round(Number(value)*100)}%</output></span>`
    :`<input type="${f.kind==='num'?'number':'text'}" id="${id}" value="${esc(optionValue(value))}" placeholder="${esc(t(f.placeholder||''))}" ${f.attrs||''}>`;
  return `<label for="${f.kind==='band'?`${id}-lo`:id}">${esc(t(f.label))}</label>${input}`;
}
const optionSectionOpen=new Map();  // section title → open, as the user left it (this session)
function renderOptionsForm(values) {
  const d=optionsState.data, list=optionsState.media?d.media_modes:d.modes, defaultMode=list.find(m=>m.default)?.mode;
  const mode=list.some(m=>m.mode===values.mode)?values.mode:defaultMode;
  const modes=`<div class="option-modes" role="radiogroup" aria-label="${t('Execution mode')}">${list.map(m=>{const reason=ts(m.reason,m.reason_code,m.reason_params);return `<label class="option-mode${m.available?'':' unavailable'}" title="${esc(reason||'')}"><input type="radio" name="optMode" value="${esc(m.mode)}"${m.mode===mode?' checked':''}${m.available?'':' disabled'}> <span>${esc(m.mode)}${m.default?` <span class="chip">${t('default')}</span>`:''}</span>${reason?`<small>${esc(reason)}</small>`:''}</label>`;}).join('')}</div>${optionsState.media?`<p class="muted">${t('With media (--media on) the model is served with --mode eager, or with --mode vllm on an NVIDIA GPU with basal[vllm].')}</p>`:''}`;
  const experimental='<p class="muted option-note" id="optExperimentalNote"></p>';
  // collapsible sections: Engine and any section with a non-default value open, unless the user toggled it this session
  const changed=keys=>keys.some(k=>JSON.stringify(values[k])!==JSON.stringify(d.defaults[k]));
  const isOpen=(title,keys)=>optionSectionOpen.get(title)??(title==='Engine'||changed(keys));
  $('#optionsForm').innerHTML=OPTION_SECTIONS.map(([title,keys])=>`<details class="option-section" data-section="${esc(title)}"${isOpen(title,keys)?' open':''}><summary>${esc(t(title))}</summary>${title==='Engine'?modes:''}<div class="option-grid">${keys.map(k=>`<div class="opt-field" data-opt="${k}"${OPTION_FIELDS[k].modes?` data-modes="${OPTION_FIELDS[k].modes.join(' ')}"`:''}>${optionInput(k,values[k])}</div>`).join('')}</div>${title==='Experimental'?experimental:title==='Caching'?`<p class="muted">${t('State cache reuses the longest cached prefix of growing or edited states, then computes the changed suffix. Its GB budget consumes GPU memory alongside weights and graphs; reduce it if memory is tight. No cache-hit counters are reported.')}</p>`:title==='Kernels (CUDA graph engine)'?`<p class="muted">${t('auto uses compact rows where supported. default uses padded graphs; fine captures more shapes and takes longer to start. compact needs supported attention kernels and more GPU memory (about 6 GB extra on measured RTX 3090 runs). Unsupported combinations show the engine’s refusal in the model row and Logs.')}</p>`:title==='Engine'?'<p class="muted" id="optPackedNote"></p>':''}</details>`).join('');
  $('#optionsForm').querySelectorAll('details.option-section > summary').forEach(s=>s.onclick=()=>optionSectionOpen.set(s.parentElement.dataset.section,!s.parentElement.open));  // a user's click (or Enter / Space), not the initial open
  $('#optionsForm').querySelectorAll('input[type=range]').forEach(r=>r.oninput=()=>{$(`#${r.id}-out`).textContent=`${r.value}%`;});
  $('#optionsForm').onchange=()=>{updateOptionVisibility();renderOptionsDiff();};
  $('#optionsForm').oninput=renderOptionsDiff;
  updateOptionVisibility();
}
function setOptionsCustom(custom) {
  const d=optionsState.data;
  if(optionsState.custom&&!custom)optionsState.customValues=collectOptions();  // back to Custom restores them
  optionsState.custom=custom;
  document.querySelectorAll('input[name=optionsChoice]').forEach(r=>{r.checked=r.value===(custom?'custom':'recommended');});
  $('#optionsForm').hidden=false;
  renderOptionsForm(custom?{...d.defaults,...optionsState.customValues}:d.defaults);
  $('#optionsForm').hidden=!custom; $('#loadPresetControls').hidden=!custom; $('#optionsReset').hidden=!custom;
  $('#optionsRecommendedNote').hidden=custom;
  $('#optionsRecommendedNote').textContent=t('basal-ui picks the execution mode ({mode}) and the settings for this machine. Choose Custom to change them.',{mode:optionModes().find(m=>m.default)?.mode||'—'});
  renderOptionsDiff();
}
const optionLabel=key=>key==='mode'?t('Execution mode'):t(OPTION_FIELDS[key]?.label||key);
const optionText=v=>v===true?t('on'):v===false?t('off'):v==null?t('default'):Array.isArray(v)?v.join('–'):String(v);
const optionChips=options=>Object.entries(options).map(([k,v])=>html`<span class="chip">${optionLabel(k)}: ${optionText(v)}</span>`.text).join(' ');
// what differs from the defaults (the settings Load, Restart or Save apply); with a running model its Active load settings
function renderOptionsDiff() {
  if(!optionsState)return;
  const changed=optionsState.custom?collectOptions():{}, loaded=(statusCache?.models||[]).find(m=>m.id===optionsState.id);
  const active=loaded?Object.fromEntries(Object.entries(loaded.options||{}).filter(([k,v])=>v!=null&&JSON.stringify(v)!==JSON.stringify(optionsState.data.defaults[k]))):null;
  const rows=[];
  if(optionsState.custom)rows.push(Object.keys(changed).length?`<strong>${t('Changed from defaults')}</strong> ${optionChips(changed)}`:`<strong>${t('Changed from defaults')}</strong> <span class="muted">${t('nothing yet')}</span>`);
  if(active)rows.push(`<strong>${t('Active load settings')}</strong> ${Object.keys(active).length?optionChips(active):`<span class="muted">${t('the defaults')}</span>`}`);
  $('#optionsDiff').innerHTML=rows.map(r=>`<p>${r}</p>`).join('');
  $('#optionsDiff').hidden=!rows.length;
}
const optionModes=()=>optionsState.media?optionsState.data.media_modes:optionsState.data.modes;
function selectedOptionMode() { return $('#optionsForm input[name=optMode]:checked')?.value||optionModes().find(m=>m.default)?.mode; }
function updateOptionVisibility() {
  const mode=selectedOptionMode(), think=optionsState.data.think||{};
  const packed=$('#opt-mlx_packed'), packedReason=optionsState.data.mlx_packed_reason;
  const packedReasonText=ts(packedReason,optionsState.data.mlx_packed_reason_code,optionsState.data.mlx_packed_reason_params);
  packed.disabled=Boolean(packedReason)||optionsState.media||!['mlx','mlx-q8'].includes(mode);
  $('#optPackedNote').textContent=mode==='mps'?t('Packed PyTorch execution: choose Device cpu for the CPU path, or mps for Apple Silicon. The CPU default remains eager.'):packedReasonText||t('MLX packed is off by default. Measured on an Apple M5 Pro (basal 2.3.1, #36): basal-2.0-4.5B 134 → 88 ms per two-order decision, mini 47 → 31 ms, the same decisions as the KV-cache path in bf16 (mlx-q8 on the 4.5B: 0.955 instead of 0.977 agreement). Compatible Llama groups with prefix plus all suffixes up to 512 tokens use packed rows; longer groups keep the KV-cache path.');
  $('#opt-device option[value=cuda]').disabled=mode==='mps';
  $('#optionsForm').querySelectorAll('[data-modes]').forEach(x=>x.hidden=!x.dataset.modes.split(' ').includes(mode));
  // thinking: basal-2.0 4.5B with vLLM (here, in another environment, or a running server); early exit: fast-exit
  const thinkBox=$('#opt-think'), exitBox=$('#opt-early_exit');
  thinkBox.disabled=!think.model; if(!think.model)setChecked(thinkBox,false);
  Object.entries(OPTION_FIELDS).filter(([,f])=>f.think).forEach(([k])=>{$(`[data-opt="${k}"]`).hidden=!thinkBox.checked;});
  exitBox.disabled=mode!=='fast-exit'; if(exitBox.disabled)exitBox.value='off';
  const notes=[];
  if(!think.model)notes.push(ts(think.reason,think.reason_code,think.reason_params));
  else if(!think.vllm)notes.push(t('vLLM is not installed in basal-ui’s environment: give the Python of one that has it, or the URL of a running think server.'));
  if(mode!=='fast-exit')notes.push(t('Early exit needs --mode fast-exit (the exit heads of basal-1.0, bf16 on a CUDA GPU).'));
  // LoRA adapters at load: only with an engine that takes --adapter (basal-ui's feature probe)
  const adapterBox=$('#opt-adapter'), adapterReason=optionsState.data.adapter_reason;
  if(adapterBox){adapterBox.disabled=Boolean(adapterReason); if(adapterReason){adapterBox.value='';adapterBox.title=ts(adapterReason,optionsState.data.adapter_reason_code,optionsState.data.adapter_reason_params);}else adapterBox.title='';}
  $('#optExperimentalNote').textContent=notes.join(' ');
}
function collectOptions() {
  const d=optionsState.data, out={}, defaultMode=optionModes().find(m=>m.default)?.mode, mode=selectedOptionMode();
  out.mode=mode===defaultMode?null:mode;
  for(const [key,f] of Object.entries(OPTION_FIELDS)) {
    if(f.kind==='band'){const lo=$(`#opt-${key}-lo`)?.value, hi=$(`#opt-${key}-hi`)?.value;out[key]=!lo||!hi?d.defaults[key]:[Number(lo),Number(hi)];continue;}
    const el=$(`#opt-${key}`);
    if(!el||el.closest('[hidden]')||el.disabled)continue;  // a field of another mode, or unavailable, keeps its default
    if(f.kind==='bool')out[key]=el.checked;
    else if(f.kind==='percent')out[key]=Number(el.value)===Math.round(d.defaults[key]*100)?d.defaults[key]:Number(el.value)/100;
    else if(f.kind==='num')out[key]=el.value===''?d.defaults[key]:Number(el.value);
    else if(f.kind==='select')out[key]=el.value===''?null:el.value;
    else out[key]=el.value.trim()||d.defaults[key];
  }
  return Object.fromEntries(Object.entries(out).filter(([k,v])=>JSON.stringify(v)!==JSON.stringify(d.defaults[k])));
}
async function openLoadOptions(id, media=false, action='load') {
  const data=await api(`/api/models/options?id=${encodeURIComponent(id)}`);
  const loaded=(statusCache?.models||[]).find(m=>m.id===id), restart=action==='restart', save=action==='save';
  optionsState={id,media,restart,save,data};
  $('#optionsModel').textContent=`${id}${media?` · ${t('with media')}`:''}${restart?` · ${t('restarts the running model')}`:save?` · ${t('kept for the next load')}`:''}`;
  $('#optionsApply').textContent=restart?t('Restart'):save?t('Save'):t('Load');
  $('#optionsSetupNote').hidden=!save||!(modelData.find(m=>m.id===id)?.media||[]).length;  // only a media model has the choice
  $('#optionsRemember').closest('label').hidden=save;  // Save always remembers
  setChecked($('#optionsRemember'),data.saved);
  $('#optionsForm').hidden=false; optionsState.custom=false;
  renderOptionsForm(restart&&loaded?{...data.defaults,...loaded.options}:data.options);
  optionsState.customValues=collectOptions(); setOptionsCustom(Object.keys(optionsState.customValues).length>0);
  $('#optionsError').hidden=true;
  await loadPresets();renderLoadPresets();
  $('#loadPresetSelect').value='';
  $('#deleteLoadPreset').disabled=true;
  $('#loadOptions').showModal();
  $('#optionsForm').scrollTop=0;  // the dialog is reused: another model's options start at the top
}
document.addEventListener('click',e=>{const b=e.target.closest('.options-model');if(b)act(()=>busyWhile(b,()=>openLoadOptions(b.dataset.id,b.dataset.media==='true',b.dataset.action)));});
$('#optionsCancel').onclick=()=>$('#loadOptions').close();
$('#optionsReset').onclick=()=>{renderOptionsForm(optionsState.data.defaults);renderOptionsDiff();};
$('#optionsApply').onclick=async()=>{
  const {id,media,restart,save,data}=optionsState, options=collectOptions(), remember=save||$('#optionsRemember').checked;
  const refocus=holdFocus($('#optionsApply'));
  $('#optionsApply').disabled=true;
  try{
    if(remember||data.saved)await api('/api/models/options',{method:'PUT',body:JSON.stringify({id,options:remember?options:{}})});
    if(save){$('#loadOptions').close();toast(t('Load options saved for {model}',{model:shortName(id)}));await loadModels();return;}
    if(restart)await api('/api/restart',{method:'POST',body:JSON.stringify({id,options})});
    else{statusSequence++;clearSelectedMedia();await api('/api/load',{method:'POST',body:JSON.stringify({id,...(media?{media:true}:{}),options})});}
    $('#loadOptions').close(); toast(restart?t('Restarting {model}',{model:shortName(id)}):t('Model load started')); await refreshStatus();
  }catch(e){$('#optionsError').textContent=e.message;$('#optionsError').hidden=false;}
  finally{$('#optionsApply').disabled=false;refocus();}
};
