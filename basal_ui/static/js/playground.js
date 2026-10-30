// Playground request: media, examples, presets and the question editor that builds the request.
'use strict';
function mediaTitle(kinds) { return kinds.map(kind => kind === 'image' ? t('images') : kind === 'audio' ? t('audio') : kind).join(` ${t('and')} `); }
function clearSelectedMedia() {
  mediaSelection++;
  clearElementQuestions();
  selectedMedia.forEach(item => URL.revokeObjectURL(item.url));
  selectedMedia = [];
  $('#mediaInput').value = '';
  renderSelectedMedia(); syncRequest();
}
const fileSize = n => n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`;
function renderSelectedMedia() {
  $('#mediaItems').innerHTML = selectedMedia.map((item,index) => `<div class="media-item">${item.kind === 'image'
    ? `<button type="button" class="media-preview" data-preview="${index}" title="${t('Open preview')}"><img src="${esc(item.url)}" alt="${esc(item.file.name)}"></button>`
    : `<audio class="media-audio" controls preload="metadata" src="${esc(item.url)}"></audio>`}${item.kind==='image'?`<button type="button" class="mark-elements" data-mark="${index}"${selectedMedia.some((m,i)=>i!==index&&m.elements)?' hidden':''} title="${selectedMedia.some((m,i)=>i!==index&&m.elements)?t('Only one image can have marked elements at a time'):t('Mark elements')}">${t('Mark elements')}</button>`:''}<div class="media-meta"><span><span class="media-name" title="${esc(item.file.name)}">${esc(item.file.name)}</span><small data-dims="${index}">${fileSize(item.file.size)}</small></span><button type="button" data-remove="${index}" aria-label="${t('Remove {name}',{name:esc(item.file.name)})}">${t('Remove')}</button></div></div>`).join('');
  $('#mediaItems').querySelectorAll('[data-mark]').forEach(button=>button.addEventListener('click',()=>openElementsEditor(Number(button.dataset.mark))));
  $('#mediaItems').querySelectorAll('[data-remove]').forEach(button => button.addEventListener('click', () => {
    const item=selectedMedia[Number(button.dataset.remove)];
    if(item.elements)removeElementQuestion(item);
    URL.revokeObjectURL(item.url);
    selectedMedia.splice(Number(button.dataset.remove),1);
    syncElementIndices();
    renderSelectedMedia(); syncRequest();
  }));
  $('#mediaItems').querySelectorAll('[data-preview]').forEach(button => {
    const item = selectedMedia[Number(button.dataset.preview)], img = button.querySelector('img');
    const showDims = () => { item.dims = `${img.naturalWidth}×${img.naturalHeight} · ${fileSize(item.file.size)}`; $(`[data-dims="${button.dataset.preview}"]`).textContent = item.dims; };
    if (img.complete && img.naturalWidth) showDims(); else img.addEventListener('load', showDims, {once:true});
    button.addEventListener('click', () => {
      $('#mediaLightboxImage').src = item.url; $('#mediaLightboxImage').alt = item.file.name;
      $('#mediaLightboxName').textContent = item.file.name; $('#mediaLightboxSize').textContent = item.dims || fileSize(item.file.size);
      $('#mediaLightbox').showModal();
    });
  });
  $('#mediaOptions').hidden = !selectedMedia.some(item => item.kind === 'image');
  $('#mediaField').hidden = !loadedMedia.length && !selectedMedia.length;  // media of a loaded example stays visible
  $('#mediaInput').disabled = !loadedMedia.length;
  updateMediaEvidence();
}
function showMediaError(message='') {
  $('#mediaError').textContent = message;
  $('#mediaError').hidden = !message;
}
async function selectMedia(files) {
  const selection = ++mediaSelection;
  showMediaError();
  if (files.length > 8) { showMediaError(t('at most 8 media items')); return; }
  const supported = new Set(loadedMedia);
  for (const file of files) {
    if (file.size > 20 * 1024 * 1024) { showMediaError(t('{name} is over 20 MiB',{name:file.name})); return; }
    const kind = file.type.startsWith('image/') ? 'image' : file.type.startsWith('audio/') ? 'audio' : null;
    if (!kind) { showMediaError(t('{name} must be an image or audio file',{name:file.name})); return; }
    if (!supported.has(kind)) { showMediaError(t('This model does not support {kind}',{kind})); return; }
  }
  const readFile = file => new Promise((resolve,reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({file,kind:file.type.startsWith('image/')?'image':'audio',data:String(reader.result).split(',')[1]});
    reader.onerror = () => reject(new Error(t('Could not read {name}',{name:file.name})));
    reader.readAsDataURL(file);
  });
  try {
    const filesData = await Promise.all(files.map(readFile));
    if (selection !== mediaSelection) return;
    clearElementQuestions();
    selectedMedia.forEach(item => URL.revokeObjectURL(item.url));
    selectedMedia = filesData.map(item => ({...item,url:URL.createObjectURL(item.file)}));
    renderSelectedMedia(); syncRequest();
  } catch (error) { if (selection === mediaSelection) showMediaError(error.message); }
}
async function refreshStatus() {
  const sequence = ++statusSequence;
  try {
    const status = await api('/api/status');
    if (sequence === statusSequence) { renderStatus(status); if (serveData) renderServeStatus(status); }
  } catch(e) {
    if (sequence === statusSequence) { showStatusError(e.message); clearTimeout(pollTimer); pollTimer=setTimeout(refreshStatus,5000); }
  }
}
async function loadExamples() {
  try {
    examples=await api('/api/examples');

    renderRequestPresets();
  } catch { examples=[];renderRequestPresets(); }
}
// Question builder: one card per question, a type-specific fieldset; fields the builder does not own stay on the card
// (card._extra) and are merged back, so Edit JSON → builder → JSON keeps them.
const QUESTION_TYPES=['choice','noul','score','multi','act','tool','classify','verify','compress','extract'];
const EVIDENCE_TYPES=['choice','noul','score','act','tool'], MAX_ERROR_TYPES=['act','tool','classify','verify','extract'];
const TASK_TYPES=['classify','verify','compress','extract'], TOOL_EXTRAS=['answer_directly','ask_user','none'];
const SET_KEYS=['objective','threshold','min','max','fallback','fallback_threshold','exclusive'];
let presetData={load:{},request:{}},presetTarget='load';
async function loadPresets() {try{presetData=await api('/api/presets');renderRequestPresets();renderLoadPresets();}catch{}}
function renderRequestPresets() {
  const select=$('#examples'), current=select.value;
  const saved=Object.keys(presetData.request||{});
  const mediaSuffix=e=>{const kinds=[...new Set((e.request?.media||[]).map(m=>m.type))];return kinds.length?` · ${esc(mediaTitle(kinds))}`:'';};
  select.innerHTML=`<option value="">${t('Load an example…')}</option>`+examples.map((e,i)=>`<option value="${i}">${esc(e.file||e.id||t('Example {number}',{number:i+1}))}${e.file&&e.id?` / ${esc(e.id)}`:''}${mediaSuffix(e)}</option>`).join('')+`<optgroup label="${t('Saved requests')}">${saved.map(n=>`<option value="preset:${esc(n)}">${esc(n)}</option>`).join('')}</optgroup>`;
  if(current&&[...select.options].some(o=>o.value===current))select.value=current;
}
function renderLoadPresets() {
  const select=$('#loadPresetSelect'); if(!select)return;
  select.innerHTML=`<option value="">${t('Choose load preset…')}</option>`+Object.keys(presetData.load||{}).map(n=>`<option value="${esc(n)}">${esc(n)}</option>`).join('');
}
function promptPresetName(target) {
  presetTarget=target; $('#presetNameTitle').textContent=target==='load'?t('Save load preset'):t('Save request preset');
  $('#presetName').value=''; $('#presetNameDialog').showModal(); $('#presetName').focus();
}
$('#presetNameSave').onclick=()=>act(async()=>{
  const name=$('#presetName').value.trim(); if(!name)return;
  let value;
  try { value=presetTarget==='load'?collectOptions():$('#editJsonToggle').checked?JSON.parse($('#requestJson').value):buildRequest(); if(presetTarget==='request')value._elements=elementPresetData(); }
  catch(e) { toast(e.message,'error');$('.q-name:invalid')?.reportValidity();return; }
  presetData=await api('/api/presets',{method:'PUT',body:JSON.stringify({kind:presetTarget,name,value})});
  $('#presetNameDialog').close(); renderLoadPresets();renderRequestPresets();toast(t('Preset saved'));
});
$('#examples').addEventListener('change',e=>{
  const val=e.target.value;
  if(val.startsWith('preset:')){const name=val.slice(7),stored=presetData.request?.[name];if(stored){const {_elements:metadata={},...request}=stored;let mediaNames=metadata.mediaNames||[],records=metadata.marked||[];if(Array.isArray(metadata)){records=metadata;mediaNames=[];metadata.forEach(record=>{mediaNames[record.index]=record.markedName;});}if(!setRequest(request,mediaNames))restorePresetMediaFallback(request,mediaNames);restoreElementMarks(records);}return;}
  const item=examples[Number(val)];
  if(item?.request){
    const request={...item.request};delete request.id;delete request.gold;setRequest(request,item.media_files||[]);
    if(item.elements&&selectedMedia[item.elements.image]){const media=selectedMedia[item.elements.image];media.elements=item.elements;const card=[...document.querySelectorAll('.question')].find(q=>$('.q-name',q).value===item.elements.question);if(card){card._elementOwner=item.elements.image;card._elementItem=media;card.dataset.elementOwner=item.elements.image;card.querySelector('.q-name').addEventListener('input',event=>{media.elements.question=event.target.value;});}}
    const needs=[...new Set((request.media||[]).map(m=>m.type))].filter(k=>!loadedMedia.includes(k));
    if(needs.length)showMediaError(t('This example carries {media}: load a basal-omni model with "Load with media", then pick this example again to send it.',{media:mediaTitle(needs)}));
  }else if(item)setRequest({state:item.state,questions:item.questions});
});
async function deletePreset(kind,name) {
  presetData=await api('/api/presets',{method:'PUT',body:JSON.stringify({kind,name,value:null})});
  renderLoadPresets();renderRequestPresets();toast(t('Preset deleted'));
}
$('#exportPresets').onclick=()=>act(async()=>{
  const data=await api('/api/presets'),a=document.createElement('a'),url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));
  a.href=url;a.download='presets.json';a.click();URL.revokeObjectURL(url);
});
$('#importPresetsButton').onclick=()=>$('#importPresets').click();
$('#importPresets').onchange=()=>act(async()=>{
  const file=$('#importPresets').files[0];if(!file)return;
  const data=JSON.parse(await file.text());let count=0;
  for(const kind of ['load','request'])for(const [name,value] of Object.entries(data[kind]||{})){await api('/api/presets',{method:'PUT',body:JSON.stringify({kind,name,value})});count++;}
  await loadPresets();toast(t('Imported {count} presets',{count}));$('#importPresets').value='';
});
$('#examples').insertAdjacentHTML('afterend',`<button type="button" id="saveRequestPreset">${t('Save request…')}</button><button type="button" id="deleteRequestPreset">${t('Delete request')}</button>`);
$('#saveRequestPreset').onclick=()=>promptPresetName('request');
$('#deleteRequestPreset').onclick=()=>act(async()=>{const v=$('#examples').value;if(!v.startsWith('preset:'))return toast(t('Choose a saved request first'),'error');await deletePreset('request',v.slice(7));$('#examples').value='';});
const OWNED={choice:['criteria','levels','option_keys'],noul:['criteria','option_keys'],score:['criteria','levels','option_keys'],
  multi:['criteria','option_keys',...SET_KEYS],act:['criteria','base','costs','defer_action','option_keys'],
  tool:['tools','extra','rank','multi','description_chars',...SET_KEYS],classify:['labels','criteria','multi_label','none','threshold','min','max','objective'],
  verify:['claims','binary'],compress:['unit','threshold','keep_ratio','min_keep'],extract:['schema']};
const KEYED=/^([^\s:]{1,40}):\s*(\S.*)$/;  // "key: description"; a plain option text may contain colons too
const textOf=v=>typeof v==='string'?v:JSON.stringify(v);
const splitLines=text=>String(text||'').split('\n').map(x=>x.trim()).filter(Boolean);
function parseOptions(text, type) {
  const lines=splitLines(text);
  if(type==='noul') { const c={}; if(lines[0]) c.true=lines[0]; if(lines[1]) c.false=lines[1]; return Object.keys(c).length?{criteria:c}:{}; }
  if(type==='score') return {criteria:lines};
  const hasDescriptions=lines.length>0&&lines.every(x=>KEYED.test(x));
  const criteria={};
  lines.forEach((line,i)=>{const m=line.match(KEYED); if(hasDescriptions) criteria[m[1]]=m[2]; else criteria[String(i)]=line;});
  return {criteria,option_keys:hasDescriptions?'show':'hide'};
}
// lines -> {key: description} when every line is "key: description", else the plain lines (a list)
function parseKeyedList(text) {
  const lines=splitLines(text);
  if(lines.length&&lines.every(x=>KEYED.test(x)))return Object.fromEntries(lines.map(x=>{const m=x.match(KEYED);return [m[1],m[2]];}));
  return lines;
}
function keyedText(value, hideKeys=false) {
  if(Array.isArray(value))return value.map(textOf).join('\n');
  if(value&&typeof value==='object')return hideKeys?Object.values(value).map(textOf).join('\n'):Object.entries(value).map(([k,v])=>v==null?String(k):`${k}: ${textOf(v)}`).join('\n');
  return '';
}
const isYesNo=q=>q.base==='noul'||Boolean(q.criteria&&!Array.isArray(q.criteria)&&typeof q.criteria==='object'&&Object.keys(q.criteria).length&&Object.keys(q.criteria).every(k=>k==='true'||k==='false'));
function criteriaText(q, type) {
  const criteria=q.criteria ?? q.levels ?? {};
  if(type==='noul'||(type==='act'&&isYesNo(q)))return Array.isArray(criteria)?'':[criteria.true,criteria.false].filter(x=>x!=null).map(textOf).join('\n');
  return keyedText(criteria, q.option_keys==='hide'&&!Array.isArray(criteria));
}
function parseStructuredText(text) { const trimmed=text.trim(); if(trimmed.startsWith('{')||trimmed.startsWith('[')) { try{return JSON.parse(trimmed);}catch{}} return text; }
let questionFieldId=0;
const qField=(label,input,help='',cls='')=>{const id=`question-field-${++questionFieldId}`;return `<div class="qf${cls?` ${cls}`:''}"><label id="${id}-label" for="${id}">${t(label)}</label>${input.replace(/<(input|textarea|select)\b/,`<$1 id="${id}" aria-labelledby="${id}-label"`)}${help?`<small class="muted">${t(help)}</small>`:''}</div>`};
const qNum=(f,v,attrs='')=>`<input class="input" type="number" data-f="${f}" aria-label="${esc(t(f.replaceAll('_',' ')))}" value="${esc(v??'')}" ${attrs}>`;
const qText=(f,v,ph='')=>`<input class="input" type="text" data-f="${f}" aria-label="${esc(t(f.replaceAll('_',' ')))}" value="${esc(v??'')}" placeholder="${esc(t(ph))}">`;
const qArea=(f,v,ph='',cls='')=>`<textarea class="textarea${cls?` ${cls}`:''}" data-f="${f}" aria-label="${esc(t(f.replaceAll('_',' ')))}" placeholder="${esc(cls==='json'?ph:t(ph))}">${esc(v??'')}</textarea>`;
const qSwitch=(f,v,label,cls='')=>`<label class="qf-switch${cls?` ${cls}`:''}"><input class="switch" type="checkbox" role="switch" data-f="${f}"${v?' checked aria-checked="true"':' aria-checked="false"'}> ${t(label)}</label>`;
const qSelect=(f,options,v)=>`<select class="select" data-f="${f}" aria-label="${esc(t(f.replaceAll('_',' ')))}">${options.map(([value,label])=>`<option value="${esc(value)}"${String(value)===String(v??'')?' selected':''}>${esc(t(label))}</option>`).join('')}</select>`;
// the set options of multi, multi-label classify and multi tool; constraints: fallback and exclusive keys (multi and
// tool only: classify takes objective, threshold, min and max)
function setFields(q, constraints=true) {
  const objective=q.objective||'threshold';
  const extra=constraints?`${qField('Fallback key',qText('fallback',q.fallback,'none'))}${qField('Fallback threshold',qNum('fallback_threshold',q.fallback_threshold,'min="0" max="1" step="0.01"'))}${qField('Exclusive keys',qText('exclusive',(q.exclusive||[]).join(', '),'comma-separated'))}`:'';
  return `<div class="qf-row set-fields">${qField('Objective',qSelect('objective',[['threshold','threshold'],['exact','exact'],['iou','iou'],['f1','f1']],objective))}${qField('Threshold',qNum('threshold',q.threshold??'','min="0" max="1" step="0.01" placeholder="0.5"'),'','set-threshold')}${qField('Min',qNum('min',q.min,'min="0" step="1"'))}${qField('Max',qNum('max',q.max,'min="1" step="1"'))}${extra}</div>`;
}
function costRowsOf(costs) {
  return costs&&typeof costs==='object'&&!(Object.keys(costs).every(k=>k==='wrong'||k==='defer')&&typeof costs.wrong!=='object')
    ?Object.entries(costs).map(([action,row])=>({action,costs:{...(row||{})}})):null;
}
function actFields(card, q) {
  const simple=q.costs&&!costRowsOf(q.costs)?q.costs:{wrong:10,defer:1};
  card._costMode=costRowsOf(q.costs)?'matrix':'simple'; card._costRows=costRowsOf(q.costs)||[]; card._costOutcomes=null;
  return qSwitch('yesno',isYesNo(q),'Yes / no outcomes')+optionField('criteria',criteriaText(q,'act'),t('Outcomes'),t('With keys, the cost matrix names its outcomes by them.'),t('Add outcome'))
    +`<div class="qf"><span class="qf-label">${t('Costs')}</span><div class="tabs cost-mode"><button type="button" data-cost-mode="simple">${t('Simple')}</button><button type="button" data-cost-mode="matrix">${t('Matrix')}</button></div>`
    +`<div class="qf-row cost-simple">${qField('Wrong action',qNum('cost_wrong',simple.wrong,'step="any"'))}${qField('Defer to a person',qNum('cost_defer',simple.defer,'step="any" placeholder="none"'))}</div>`
    +`<div class="cost-matrix"><div class="table-wrap"><table class="cost-table"></table></div><button type="button" class="add-action">${t('Add action')}</button>${qField('Defer action',qText('defer_action',q.defer_action,'defer or human'),'The action max_error falls back to.')}</div></div>`;
}
function renderTypeFields(card, type, q) {
  let html='';
  if(type==='choice')html=optionField('criteria',criteriaText(q,type),t('Options'),t('Fill every key to send explicit keys; leave all keys empty to use hidden keys 0…n.'));
  else if(type==='noul')html=optionField('criteria',criteriaText(q,type),t('Yes / no descriptions'),t('Optional: what yes and no mean for this question.'));
  else if(type==='score')html=optionField('criteria',criteriaText(q,type),t('Score levels'),t('Ordered from lowest to highest score.'),t('Add level'));
  else if(type==='multi')html=optionField('criteria',keyedText(q.criteria),t('Labels'),t('Each label is its own yes/no decision; the selected set follows the objective.'),t('Add label'))+setFields(q);
  else if(type==='act')html=actFields(card,q);
  else if(type==='tool'){
    const extra=q.extra??(q.multi?[]:TOOL_EXTRAS);
    html=qField('Tools (JSON array)',qArea('tools',q.tools?JSON.stringify(q.tools,null,2):'','[{"name": "get_weather", "description": "…", "inputSchema": {"type": "object", "properties": {"city": {"type": "string"}}}}]','json'),'OpenAI function, OpenAI Responses, MCP tools/list or Anthropic definitions.')
      +`<div class="qf"><span class="qf-label">${t('Other options')}</span><div class="controls">${TOOL_EXTRAS.map(x=>`<label><input class="switch" type="checkbox" role="switch" data-extra="${x}"${extra.includes(x)?' checked aria-checked="true"':' aria-checked="false"'}> ${t(x)}</label>`).join('')}</div></div>`
      +`<div class="qf-row">${qField('Rank',qNum('rank',q.rank,'min="1" step="1" placeholder="off"'),'also the k most probable options')}${qField('Description chars',qNum('description_chars',q.description_chars,'min="0" step="1" placeholder="300"'),'0 = no cap')}</div>`+qSwitch('multi',q.multi,'Several tools (multi)')
      +`<div class="multi-tool-only">${setFields(q)}<small class="muted">${t('The other options (answer directly, ask the user, none) are always exclusive.')}</small></div>`;
  }
  else if(type==='classify')html=optionField('labels',labelsText(q.labels??q.criteria),t('Labels'),t('Fill every key to send explicit keys; leave all keys empty to send the labels as a list.'),t('Add label'))+qSwitch('multi_label',q.multi_label,'Several labels (multi-label)')+' '+qSwitch('none',q.none,'Add a "none" label','single-only')+`<div class="multi-only">${setFields(q,false)}</div>`;
  else if(type==='verify')html=optionField('claims',keyedText(q.claims),t('Claims'),t('Fill every key to name the claims; leave all keys empty to number them 0…n.'),t('Add claim'))+qSwitch('binary',q.binary,'Binary (supported / not supported)');
  else if(type==='compress')html=`<div class="qf-row">${qField('Unit',qSelect('unit',[['sentence','sentence'],['line','line'],['paragraph','paragraph']],q.unit||'sentence'))}${qField('Threshold',qNum('threshold',q.threshold,'min="0" max="1" step="0.05" placeholder="0.5"'))}${qField('Keep ratio',qNum('keep_ratio',q.keep_ratio,'min="0" max="1" step="0.05" placeholder="off"'),'share of the characters kept (overrides threshold)')}${qField('Min keep',qNum('min_keep',q.min_keep,'min="0" step="1" placeholder="1"'))}</div>`;
  else if(type==='extract')html=qField('Schema (JSON)',qArea('schema',q.schema?JSON.stringify(q.schema,null,2):'','{"type": "object", "properties": {"total": {"type": "number"}}}','json'),'string, number, integer, boolean, enum and arrays of enums; nested objects as dotted paths.');
  const me=q.max_error, preset=me==null||Array.isArray(me)?'':[0.01,0.05].includes(Number(me))?String(Number(me)):'custom', maxErrorId=`question-field-${++questionFieldId}`;
  html+=`<div class="qf-row common-fields">${qSwitch('evidence',q.evidence===true,'Evidence spans','evidence-only')}<div class="qf max-error-only"><label for="${maxErrorId}">max_error</label><div class="controls">${qSelect('max_error',[['','none'],['0.01','0.01'],['0.05','0.05'],['custom','custom…']],preset).replace('<select ',`<select id="${maxErrorId}" `)}${qNum('max_error_custom',preset==='custom'?me:'','min="0" max="1" step="0.001"')}</div></div></div><div class="q-error reason wide" role="alert" hidden></div>`;
  $('.q-fields',card).innerHTML=html;
  $('.q-instructions-input',card).placeholder=['tool',...TASK_TYPES].includes(type)?t('optional'):t('What should the model decide?');
  initOptionRows(card);
  if(type==='act')renderCostMatrix(card);
  updateCardVisibility(card);
}
function actOutcomes(card) {
  if($('[data-f="yesno"]',card)?.checked)return ['true','false'];
  return Object.keys(parseOptions($('[data-f="criteria"]',card)?.value||'','choice').criteria);
}
function readCostRows(card) {
  return [...card.querySelectorAll('.cost-table tbody tr')].map(tr=>({action:$('.cost-action',tr).value,costs:Object.fromEntries([...tr.querySelectorAll('.cost-cell')].map(i=>[i.dataset.outcome,i.value]))}));
}
function renderCostMatrix(card, force=false) {
  const outcomes=actOutcomes(card), table=$('.cost-table',card);
  if(!table)return;
  if(!force&&JSON.stringify(outcomes)===JSON.stringify(card._costOutcomes))return;
  if(!force&&card._costOutcomes)card._costRows=readCostRows(card);  // forced callers set _costRows themselves
  card._costOutcomes=outcomes;
  table.innerHTML=`<thead><tr><th>${t('Action')}</th>${outcomes.map(o=>`<th>${esc(o)}</th>`).join('')}<th><span class="sr-only">${t('Remove')}</span></th></tr></thead><tbody>${card._costRows.map((row,i)=>`<tr><td><input class="input cost-action" type="text" aria-label="${t('Action {number}',{number:i+1})}" value="${esc(row.action)}"></td>${outcomes.map(o=>`<td><input class="input cost-cell" type="number" step="any" aria-label="${t('{outcome} cost for {action}',{outcome:esc(o),action:esc(row.action)})}" data-outcome="${esc(o)}" value="${esc(row.costs[o]??'')}"></td>`).join('')}<td><button type="button" class="remove-action" data-row="${i}" aria-label="${t('Remove action {number}',{number:i+1})}">${t('Remove')}</button></td></tr>`).join('')}</tbody>`;
}
function setCostMode(card, mode) {
  if(card._costOutcomes)card._costRows=readCostRows(card);
  if(mode==='matrix'&&card._costMode==='simple'&&!card._costRows.length){  // start from the simple costs
    const outcomes=actOutcomes(card), w=$('[data-f="cost_wrong"]',card).value, d=$('[data-f="cost_defer"]',card).value;
    card._costRows=outcomes.map(y=>({action:y,costs:Object.fromEntries(outcomes.map(z=>[z,z===y?0:w]))}));
    if(d!=='')card._costRows.push({action:'defer',costs:Object.fromEntries(outcomes.map(z=>[z,d]))});
    card._costOutcomes=null;
  }
  card._costMode=mode; renderCostMatrix(card,true); updateCardVisibility(card);
}
function updateCardVisibility(card) {
  const type=$('.q-type',card).value, on=f=>Boolean($(`[data-f="${f}"]`,card)?.checked), show=(sel,visible)=>card.querySelectorAll(sel).forEach(x=>x.hidden=!visible);
  const multiTool=type==='tool'&&on('multi');
  show('.evidence-only',EVIDENCE_TYPES.includes(type)&&!multiTool);
  show('.max-error-only',MAX_ERROR_TYPES.includes(type)&&!multiTool);
  const custom=$('[data-f="max_error_custom"]',card); if(custom)custom.hidden=$('[data-f="max_error"]',card).value!=='custom';
  const objective=$('[data-f="objective"]',card); show('.set-threshold',!objective||objective.value==='threshold');
  show('.multi-only',on('multi_label')); show('.single-only',type==='classify'&&!on('multi_label'));
  show('.multi-tool-only',multiTool);
  if(type==='act'){
    show('.cost-simple',card._costMode==='simple'); show('.cost-matrix',card._costMode==='matrix');
    card.querySelectorAll('[data-cost-mode]').forEach(b=>b.classList.toggle('active',b.dataset.costMode===card._costMode));
  }
}
// every value the user can change on a card (the cost matrix too): equal to the rendered one = nothing was edited
// the card's own controls: the option rows only mirror their hidden textarea (option-rows.js)
const cardControls=card=>[...card.querySelectorAll('input,select,textarea')].filter(x=>!x.matches('.opt-in'));
function cardSnapshot(card) {
  return JSON.stringify(cardControls(card).filter(x=>!x.matches('.q-name'))
    .map(x=>x.type==='checkbox'||x.type==='radio'?x.checked:x.value).concat([card._costMode??null]));
}
// the request a card was made from, to give it back exactly where the user did not edit it: the builder's own text
// forms (lines, defaults left out) cannot hold every request (a list of {name, rubric} labels, option_keys given as
// the default, non-string descriptions)
function rememberOriginal(card, type, q) {
  const value=f=>$(`[data-f="${f}"]`,card)?.value;
  card._orig={type,q:structuredClone(q),snapshot:cardSnapshot(card),
    lines:{criteria:value('criteria'),labels:value('labels'),claims:value('claims'),yesno:$('[data-f="yesno"]',card)?.checked,
           instructions:$('.q-instructions-input',card).value}};
}
const labelObjects=v=>Array.isArray(v)&&v.length>0&&v.every(x=>x&&typeof x==='object'&&!Array.isArray(x)&&'name' in x);
function labelsText(v) {  // classify labels: {key: rubric}, [keys] or [{"name", "rubric" | "description"}]
  if(!labelObjects(v))return keyedText(v);
  return v.map(x=>{const d=x.rubric??x.description;return d==null?String(x.name):`${x.name}: ${textOf(d)}`;}).join('\n');
}
function parseLabels(text, original) {
  if(!labelObjects(original))return parseKeyedList(text);
  const key=original.some(x=>'rubric' in x)||!original.some(x=>'description' in x)?'rubric':'description';
  return splitLines(text).map(line=>{const m=line.match(KEYED);return m?{name:m[1],[key]:m[2]}:{name:line};});
}
function parseQuestionCard(card, type=$('.q-type',card).value) {
  const name=$('.q-name',card).value.trim() || 'question';
  const orig=card._orig&&card._orig.type===type?card._orig:null, box=$('.q-error',card);
  if(orig&&cardSnapshot(card)===orig.snapshot){  // nothing edited: the request as it came
    card._error=null; if(box){box.textContent='';box.hidden=true;}
    return [name,structuredClone(orig.q)];
  }
  const el=f=>$(`[data-f="${f}"]`,card), str=f=>(el(f)?.value??'').trim(), num=f=>str(f)===''?undefined:Number(str(f)), on=f=>Boolean(el(f)?.checked);
  const q={type}, errors=[], set=(k,v)=>{if(v!==undefined&&v!==''&&!(Array.isArray(v)&&!v.length))q[k]=v;};
  const json=(f,check,message)=>{const text=str(f);if(!text){errors.push(t(message));return undefined;}try{const v=JSON.parse(text);if(check(v))return v;}catch{}errors.push(t(message));return undefined;};
  // a field whose text the user did not edit keeps the original's keys and values (none of them when it had none)
  const unedited=f=>orig!=null&&el(f)!=null&&el(f).value===orig.lines[f]&&(f!=='criteria'||type!=='act'||on('yesno')===orig.lines.yesno);
  const keep=(...keys)=>{keys.forEach(k=>{if(k in orig.q)q[k]=structuredClone(orig.q[k]);});};
  if(orig&&$('.q-instructions-input',card).value===orig.lines.instructions)keep('instructions');
  else{
    const instructions=parseStructuredText($('.q-instructions-input',card).value);
    if(instructions!==''||!['tool',...TASK_TYPES].includes(type))q.instructions=instructions;
  }
  const setOptions=(constraints=true)=>{
    const objective=str('objective');
    if(objective&&objective!=='threshold')q.objective=objective; else set('threshold',num('threshold'));
    set('min',num('min')); set('max',num('max'));
    if(!constraints)return;
    set('fallback',str('fallback')); set('fallback_threshold',num('fallback_threshold'));
    set('exclusive',str('exclusive').split(',').map(x=>x.trim()).filter(Boolean));
  };
  if(['choice','noul','score'].includes(type)){if(unedited('criteria'))keep('criteria','levels','option_keys');else Object.assign(q,parseOptions(str('criteria'),type));}
  else if(type==='multi'){if(unedited('criteria'))keep('criteria','option_keys');else set('criteria',parseKeyedList(str('criteria'))); setOptions();}
  else if(type==='act'){
    if(unedited('criteria'))keep('criteria','base','option_keys');
    else if(on('yesno')){const c=parseOptions(str('criteria'),'noul').criteria;if(c)q.criteria=c;else q.base='noul';}
    else Object.assign(q,parseOptions(str('criteria'),'choice'));
    if(card._costMode==='matrix'){
      const rows=readCostRows(card).filter(r=>r.action.trim());
      const missing=rows.some(r=>Object.values(r.costs).some(v=>v===''));
      if(missing)errors.push(t('every cost cell needs a number'));
      q.costs=Object.fromEntries(rows.map(r=>[r.action.trim(),Object.fromEntries(Object.entries(r.costs).filter(([,v])=>v!=='').map(([o,v])=>[o,Number(v)]))]));
      set('defer_action',str('defer_action'));
    } else {
      q.costs={wrong:num('cost_wrong')??0}; if(num('cost_defer')!==undefined)q.costs.defer=num('cost_defer');
    }
  }
  else if(type==='tool'){
    set('tools',json('tools',v=>Array.isArray(v)&&v.length>0,'tools must be a JSON array of tool definitions'));
    const multi=on('multi'), extra=TOOL_EXTRAS.filter(x=>$(`[data-extra="${x}"]`,card)?.checked);
    if(multi){q.multi=true; if(extra.length)q.extra=extra; setOptions();} else if(extra.length!==TOOL_EXTRAS.length)q.extra=extra;
    set('rank',num('rank')); if(num('description_chars')!==undefined&&num('description_chars')!==300)q.description_chars=num('description_chars');
  }
  else if(type==='classify'){
    if(unedited('labels'))keep('labels','criteria','option_keys'); else set('labels',parseLabels(str('labels'),orig?.q.labels));
    if(on('multi_label')){q.multi_label=true;setOptions(false);} else if(on('none'))q.none=true;
  }
  else if(type==='verify'){if(unedited('claims'))keep('claims');else set('claims',parseKeyedList(str('claims'))); if(on('binary'))q.binary=true;}
  else if(type==='compress'){
    if(str('unit')&&str('unit')!=='sentence')q.unit=str('unit');
    if(num('threshold')!==undefined&&num('threshold')!==0.5)q.threshold=num('threshold');
    set('keep_ratio',num('keep_ratio')); if(num('min_keep')!==undefined&&num('min_keep')!==1)q.min_keep=num('min_keep');
  }
  else if(type==='extract')set('schema',json('schema',v=>v&&typeof v==='object'&&!Array.isArray(v)&&v.properties&&typeof v.properties==='object','schema must be a JSON object with properties'));
  const multiTool=type==='tool'&&q.multi;
  if(EVIDENCE_TYPES.includes(type)&&!multiTool&&on('evidence'))q.evidence=true;
  if(MAX_ERROR_TYPES.includes(type)&&!multiTool){const me=str('max_error');if(me==='custom')set('max_error',num('max_error_custom'));else if(me)q.max_error=Number(me);}
  card._error=errors.join('\n')||null;
  if(box){box.textContent=card._error||'';box.hidden=!card._error;}
  return [name,{...card._extra,...q}];
}
function extraOf(q, type) {
  const owned=new Set(['type','instructions','evidence',...OWNED[type]]);
  if(!Array.isArray(q.max_error))owned.add('max_error');  // a list of targets (tasks) is kept as given
  return Object.fromEntries(Object.entries(q||{}).filter(([k])=>!owned.has(k)));
}
function mediaRequestFields() {
  const fields = {};
  if (selectedMedia.length) fields.media = selectedMedia.map(({kind,data}) => ({type:kind,data}));
  if (selectedMedia.some(item => item.kind === 'image')) fields.detail = $('#mediaDetail').value;
  return fields;
}
// request-level options of the playground head; in Edit JSON mode they patch the JSON
function requestOptionFields() {
  const fields={};
  if($('#factsToggle').checked)fields.facts='auto';
  if($('#langSelect').value!=='auto')fields.lang=$('#langSelect').value;
  if(!$('#thinkSelect').hidden&&$('#thinkSelect').value)fields.think=$('#thinkSelect').value;
  if(!$('#thinkTextLabel').hidden&&$('#thinkTextToggle').checked)fields.think_text=true;
  if(!$('#earlyExitSelect').hidden&&$('#earlyExitSelect').value)fields.early_exit=$('#earlyExitSelect').value;
  return fields;
}
const REQUEST_OPTION_KEYS=['facts','lang','think','think_text','early_exit'];
// think / early_exit appear for a model loaded with Thinking / with early-exit levels (--mode fast-exit)
function setPlaygroundExperimental(model) {
  const think=Boolean(model?.options?.think), levels=model?.entry?.early_exit||[], select=$('#earlyExitSelect');
  $('#thinkSelect').hidden=$('#thinkTextLabel').hidden=!think;
  if(JSON.stringify([...select.options].map(o=>o.value))!==JSON.stringify(['',...levels]))select.innerHTML=['',...levels].map(l=>`<option value="${esc(l)}">${l?t('Early exit: {level}',{level:esc(l)}):t('Early exit: server default')}</option>`).join('');
  select.hidden=!levels.length;
  renderContextPolicy();
}
function buildRequest() {
  const questions=Object.create(null), names=new Set();
  for(const card of document.querySelectorAll('.question')) {
    const input=$('.q-name',card), name=input.value, duplicate=names.has(name), invalid=!name.trim()||duplicate;
    input.setCustomValidity(!name.trim()?t('Enter a question name.'):duplicate?t('Question names must be unique.'):'');
    if(invalid)input.setAttribute('aria-invalid','true');else input.removeAttribute('aria-invalid');
    names.add(name);
    const [,q]=parseQuestionCard(card);if(!invalid)questions[name]=q;
  }
  if([...document.querySelectorAll('.q-name')].some(input=>!input.checkValidity()))throw new Error(t('Question names must be non-blank and unique.'));
  const stateInput=$('#stateInput'), state=stateInput._requestState;
  return {state:state!==undefined&&stateInput.value===state.text?state.value:parseStructuredText(stateInput.value),questions,...requestOptionFields(),...mediaRequestFields()};
}
function syncRequest(force=false, keys=['media','detail']) {
  const textarea = $('#requestJson');
  if ($('#editJsonToggle').checked && !force) {
    let request;
    try { request = JSON.parse(textarea.value); }
    catch (e) { showMediaError(e.message); return; }
    if (!request || typeof request !== 'object' || Array.isArray(request)) { showMediaError(t('request JSON must be an object')); return; }
    keys.forEach(k=>delete request[k]);
    const fields={...requestOptionFields(),...mediaRequestFields()};
    keys.forEach(k=>{if(k in fields)request[k]=fields[k];});
    textarea.value = JSON.stringify(request, null, 2);
    showMediaError();
  } else if (force || !$('#editJsonToggle').checked) {
    try { textarea.value = JSON.stringify(buildRequest(), null, 2); }
    catch (e) { showMediaError(e.message); }
  }
}
function addQuestion(name, q={type:'choice',instructions:'',criteria:{}}) {
  if(name===undefined){const names=new Set([...document.querySelectorAll('.q-name')].map(input=>input.value));name='question';for(let i=2;names.has(name);i++)name=`question${i}`;}
  const card=document.createElement('div'); card.className='question'; const fieldId=`question-card-${++questionFieldId}`;
  const type=QUESTION_TYPES.includes(q.type)?q.type:'choice';
  card._extra=extraOf(q,type); card._type=type;
  card.innerHTML=`<div class="q-head"><div><label for="${fieldId}-name">${t('Name')}</label><input id="${fieldId}-name" class="input q-name" type="text" value="${esc(name)}"></div><div><label for="${fieldId}-type">${t('Type')}</label><select id="${fieldId}-type" class="select q-type">${QUESTION_TYPES.map(type=>`<option value="${type}">${type}</option>`).join('')}</select></div><button type="button" class="remove-question">${t('Remove')}</button></div><div class="q-instructions"><label for="${fieldId}-instructions">${t('Instructions')}</label><textarea id="${fieldId}-instructions" class="textarea q-instructions-input" placeholder="${t('What should the model decide?')}">${esc(typeof q.instructions==='string'?q.instructions:q.instructions==null?'':JSON.stringify(q.instructions))}</textarea></div><div class="q-fields"></div>`;
  $('.q-type',card).value=type;
  renderTypeFields(card,type,q); rememberOriginal(card,type,q);
  card.addEventListener('input',e=>{
    if(e.target.matches('[data-f="criteria"]')&&$('.q-type',card).value==='act')renderCostMatrix(card);
    syncRequest();
  });
  card.addEventListener('change',e=>{
    if(e.target.matches('.q-type')){
      const next=e.target.value, [,current]=parseQuestionCard(card,card._type);
      card._type=next; card._extra=Object.fromEntries(Object.entries(card._extra).filter(([k])=>!OWNED[next].includes(k)));
      renderTypeFields(card,next,{...current,type:next});
    } else {
      if(e.target.matches('[data-f="yesno"]')){renderOptionRows(card);renderCostMatrix(card);}
      updateCardVisibility(card);
    }
    syncRequest();
  });
  card.addEventListener('click',e=>{
    const b=e.target.closest('button'); if(!b)return;
    if(b.matches('.remove-question')){if(card._elementItem)removeElementQuestion(card._elementItem);card.remove();syncRequest();}
    else if(b.dataset.costMode){setCostMode(card,b.dataset.costMode);syncRequest();}
    else if(b.matches('.add-action')){card._costRows=readCostRows(card);card._costRows.push({action:'',costs:{}});renderCostMatrix(card,true);syncRequest();}
    else if(b.matches('.remove-action')){card._costRows=readCostRows(card);card._costRows.splice(Number(b.dataset.row),1);renderCostMatrix(card,true);syncRequest();}
  });
  $('#questions').append(card); updateMediaEvidence(); syncRequest();
}
function setRequest(req, names=[]) {  // names: file names of req.media (bundled examples); otherwise replay.<ext>
  mediaSelection++;
  selectedMedia.forEach(item=>URL.revokeObjectURL(item.url));selectedMedia=[];$('#mediaInput').value='';renderSelectedMedia();
  const preserveJson=()=>{
    $('#editJsonToggle').checked=true;$('#editJsonArea').hidden=false;$('#builder').hidden=true;
    $('#requestJson').value=JSON.stringify(req,null,2);
    showMediaError(t('This request contains fields or values the Playground builder cannot edit. It remains in Edit JSON mode.'));
    return false;
  };
  if(!req||typeof req!=='object'||Array.isArray(req)||!Object.hasOwn(req,'state')||req.state==null||
     !Object.hasOwn(req,'questions')||!req.questions||typeof req.questions!=='object'||Array.isArray(req.questions))return preserveJson();
  const known=new Set(['state','questions','facts','lang','think','think_text','early_exit','media','detail','model']);
  const media=Array.isArray(req.media)?req.media:[];
  let restored=[];
  try {
    if(Object.keys(req).some(key=>!known.has(key))||
       Object.values(req.questions).some(q=>!q||typeof q!=='object'||Array.isArray(q)||!QUESTION_TYPES.includes(q.type))||
       (Object.hasOwn(req,'media')&&!Array.isArray(req.media))||
       media.some(item=>!item||typeof item!=='object'||Array.isArray(item)||Object.keys(item).some(k=>!['type','data'].includes(k))||
         !['image','audio'].includes(item.type)||typeof item.data!=='string'||!(/^(?:[A-Za-z0-9+/]+={0,2}|data:[^,]+;base64,[A-Za-z0-9+/]+={0,2})$/).test(item.data))||
       (Object.hasOwn(req,'detail')&&(!['default','high'].includes(req.detail)||(media.length>0&&!media.some(item=>item.type==='image'))))||
       (Object.hasOwn(req,'facts')&&req.facts!=='auto')||(Object.hasOwn(req,'lang')&&!['auto','pl','en'].includes(req.lang))||
       (Object.hasOwn(req,'think')&&!['','off','force'].includes(req.think))||(Object.hasOwn(req,'think_text')&&typeof req.think_text!=='boolean')||
       (Object.hasOwn(req,'model')&&![...$('#playgroundModel').options].some(o=>o.value===req.model))||
       (Object.hasOwn(req,'early_exit')&&!([...$('#earlyExitSelect').options].some(o=>o.value===req.early_exit))))throw new Error();
    media.forEach((item,i)=>{
      const match=item.data.match(/^data:([^;]+);base64,(.*)$/),mime=match?.[1]|| (item.type==='image'?'image/png':'audio/wav'),data=match?.[2]||item.data;
      const bytes=Uint8Array.from(atob(data),char=>char.charCodeAt(0)),file=new File([bytes],names[i]||`replay.${item.type==='image'?'png':'wav'}`,{type:mime});
      restored.push({kind:item.type,data,file,url:URL.createObjectURL(file)});
    });
  } catch {
    restored.forEach(item=>URL.revokeObjectURL(item.url));
    return preserveJson();
  }
  selectedMedia=restored;renderSelectedMedia();
  $('#editJsonToggle').checked=false;$('#editJsonArea').hidden=true;$('#builder').hidden=false;showMediaError();
  if(req.model&&[...$('#playgroundModel').options].some(o=>o.value===req.model)){
    selectedModel=req.model;localStorage.setItem('basal-ui.model',selectedModel);$('#playgroundModel').value=selectedModel;
    const model=readyModels().find(m=>m.id===selectedModel);setPlaygroundMedia(model?.media||[]);setPlaygroundExperimental(model);
  }
  const stateInput=$('#stateInput');
  stateInput.value=typeof req.state==='string'?req.state:JSON.stringify(req.state??'',null,2);
  stateInput._requestState={text:stateInput.value,value:structuredClone(req.state??'')};
  setChecked($('#factsToggle'),req.facts==='auto'); $('#langSelect').value=['pl','en'].includes(req.lang)?req.lang:'auto';
  $('#thinkSelect').value=['off','force'].includes(req.think)?req.think:''; setChecked($('#thinkTextToggle'),req.think_text===true);
  if([...$('#earlyExitSelect').options].some(o=>o.value===req.early_exit))$('#earlyExitSelect').value=req.early_exit; else $('#earlyExitSelect').value='';
  $('#mediaDetail').value=req.detail||'default';
  $('#questions').innerHTML='';
  for(const [name,q] of Object.entries(req.questions||{})) addQuestion(name,q);
  syncRequest(true);
  return true;
}
const PLAYGROUND_DRAFT='basal-ui.language-draft';
window.preparePlaygroundLanguageSwitch=()=>{
  let hadMedia=selectedMedia.length>0;
  if($('#editJsonToggle').checked){
    let text=$('#requestJson').value;
    try {const request=JSON.parse(text);if(request&&typeof request==='object'&&!Array.isArray(request)){hadMedia=hadMedia||(Array.isArray(request.media)&&request.media.length>0);if('media' in request||'_elements' in request){delete request.media;delete request._elements;text=JSON.stringify(request,null,2);}}} catch {}
    sessionStorage.setItem(PLAYGROUND_DRAFT,JSON.stringify({json:true,text,hadMedia}));
    return;
  }
  const cards=[...document.querySelectorAll('#questions .question')];
  const stateInput=$('#stateInput'), state=stateInput._requestState&&stateInput.value===stateInput._requestState.text
    ?stateInput._requestState.value:parseStructuredText(stateInput.value);
  const request={state,questions:{},...requestOptionFields(),detail:$('#mediaDetail').value||'default'};
  const names=cards.map(card=>$('.q-name',card).value);
  const controls=cards.map(card=>cardControls(card).map(el=>el.type==='checkbox'?{checked:el.checked}:{value:el.value}));
  const costModes=cards.map(card=>card._costMode);
  const costRows=cards.map(card=>card._costMode==='matrix'?readCostRows(card):null);
  cards.forEach((card,i)=>{request.questions[`__language_draft_${i}`]=parseQuestionCard(card)[1];});
  const model=selectedModel||$('#playgroundModel').value;
  if(model)request.model=model;
  sessionStorage.setItem(PLAYGROUND_DRAFT,JSON.stringify({request,hadMedia,names,controls,costModes,costRows}));
};
function restorePlaygroundLanguageDraft(){
  const saved=sessionStorage.getItem(PLAYGROUND_DRAFT);if(!saved)return;
  sessionStorage.removeItem(PLAYGROUND_DRAFT);
  try{
    const draft=JSON.parse(saved);
    if(draft.json){$('#editJsonToggle').checked=true;$('#editJsonArea').hidden=false;$('#builder').hidden=true;$('#requestJson').value=draft.text;}
    else if(draft.request){
      setRequest(draft.request);
      const cards=[...document.querySelectorAll('#questions .question')];
      cards.forEach((card,i)=>{
        if(draft.costModes?.[i]==='matrix'&&$('.q-type',card).value==='act'){
          card._costMode='matrix';
          card._costRows=draft.costRows?.[i]||[];
          card._costOutcomes=null;
          renderCostMatrix(card,true);
        }
        const controls=cardControls(card);
        (draft.controls?.[i]||[]).forEach((value,j)=>{
          const el=controls[j];if(!el)return;
          if('checked' in value)el.checked=value.checked;else el.value=value.value;
        });
        $('.q-name',card).value=draft.names?.[i]??$('.q-name',card).value;
        renderOptionRows(card); updateCardVisibility(card);
      });
      syncRequest(true);
    }
    if(draft.hadMedia)showMediaError(t('Media must be attached again after changing language.'));
  }catch{}
}
