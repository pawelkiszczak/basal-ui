// Model manager: statistics, benchmarks and calibration.
'use strict';
let statsSequence=0;
async function loadStats() {
  const sequence=++statsSequence;
  try{
    const data=await api('/api/stats');
    if(sequence!==statsSequence)return;  // a reset or a newer poll answered meanwhile
    statsData=data;
    const signature=JSON.stringify(statsData.models);
    if(signature!==statsSignature){statsSignature=signature;renderStats();if(statusCache)renderLoadedRows(statusCache.models||[]);}
    const bench=JSON.stringify(statsData.benchmarks);
    if(bench!==benchSignature){benchSignature=bench;renderBenchmarkResults();}
  }catch{}
}
function renderStats() {
  const entries=Object.entries(statsData.models||{});
  const ms=v=>`${num(v,0)} ms`;
  const card=([id,x])=>{
    const types=Object.entries(x.by_type||{}).sort(([,a],[,b])=>(b.questions||0)-(a.questions||0)).map(([type,v])=>({label:type,value:v.avg_best_prob,note:tp(v.questions,'{n} question','{n} questions')}));  // the most asked first
    return `<article class="stats-model"><div class="section-head"><h4 title="${esc(id)}">${esc(shortName(id))}</h4><button type="button" data-reset="${esc(id)}">${t('Reset')}</button></div><div class="stats"><div class="stat"><span class="stat-label">${t('Requests')}</span><span class="stat-value">${esc(x.requests)}</span><span class="stat-meta">${tp(x.questions,'{n} question','{n} questions')}${x.errors?` · <span class="reason">${tp(x.errors,'{n} error','{n} errors')}</span>`:''}</span></div><div class="stat"><span class="stat-label">${t('Avg best-answer prob.')}</span><span class="stat-value">${pct(x.avg_best_prob)}</span></div><div class="stat"><span class="stat-label">${t('Latency p50 / p95')}</span><span class="stat-value">${num(x.p50_ms,0)} / ${ms(x.p95_ms)}</span>${chartHistogram({label:t('Latency of the recent requests of {model}, in ms',{model:shortName(id)}),values:x.latency_recent_ms||[],bins:10,format:ms})}</div><div class="stat"><span class="stat-label">${t('Input tokens')}</span><span class="stat-value">${esc(x.input_tokens)}</span><span class="stat-meta">${t('Last used')}: ${x.last_used?esc(new Date(x.last_used*1000).toLocaleString()):'—'}</span></div></div>${types.length?`<h5 class="viz-title">${t('Avg best-answer prob. by question type')}</h5>${chartBars({label:t('Average best-answer probability by question type, {model}',{model:shortName(id)}),items:types,max:1,format:pct})}`:`<p class="muted">${t('no answers yet')}</p>`}</article>`;
  };
  $('#statsTable').innerHTML=`<div class="section-head"><h3>${t('Statistics')}</h3>${entries.length?`<button type="button" id="resetAllStats" class="destructive">${t('Reset all')}</button>`:''}</div>${entries.length?`<div class="stats-models">${entries.map(card).join('')}</div>`:`<div class="empty-state"><strong>${t('No decisions yet')}</strong><span>${t('No decisions yet: statistics appear after the first playground, API or MCP decision.')}</span><div class="controls"><a class="btn" href="#playground">${t('Open the Playground')}</a></div></div>`}`;
  $('#statsTable').querySelectorAll('[data-reset]').forEach(b=>b.onclick=()=>act(async()=>{if(await confirmDialog({title:t('Reset statistics?'),description:t('The counters of {model} start from zero.',{model:b.dataset.reset}),confirmLabel:t('Reset'),destructive:true})){await api(`/api/stats/${encodeURIComponent(b.dataset.reset)}`,{method:'DELETE'});toast(t('Statistics reset'));await loadStats();}}));
  const all=$('#resetAllStats'); if(all)all.onclick=()=>act(async()=>{if(await confirmDialog({title:t('Reset all statistics?'),description:t('The counters of every model start from zero. Benchmark results stay.'),confirmLabel:t('Reset all'),destructive:true})){await api('/api/stats',{method:'DELETE'});toast(t('Statistics reset'));await loadStats();}});
}
const BENCH_HINTS={benchmark:t('5 warm-up, 30 sequential and 120 concurrent requests (8 at a time); items with a gold answer are scored. Own set: {link}.',{link:'<code>docs/BENCHMARK_SETS.md</code>'}),
  loadtest:t('basal-loadtest against the model: 10 warm-up, sequential requests for the latency, then the concurrent ones for the throughput. Synthetic states of about N tokens replace the set when given.'),
  determinism:t('basal-loadtest --determinism: N requests alone, then the same N mixed with N others at the concurrency; a model loaded with Deterministic must return identical probabilities.'),
  modes:t('basal-bench in basal-ui’s process: each mode is loaded, measured and freed in turn (the first is the reference of the agreement). Needs the model’s memory once more (twice for eager-fp32) while it runs.')};
$('#benchmarks').innerHTML=`<div class="section-head"><h3>${t('Benchmarks')}</h3></div><div class="controls"><select id="benchmarkKind" aria-label="${t('Measurement')}"><option value="benchmark">${t('Benchmark (preset)')}</option><option value="loadtest">${t('Load test')}</option><option value="determinism">${t('Determinism check')}</option><option value="modes">${t('Compare engine modes')}</option></select><select id="benchmarkModel" aria-label="${t('Model to benchmark')}"></select><select id="benchmarkSet" aria-label="${t('Benchmark set')}"><option value="bundled">${tp(44,'Bundled examples ({n} item)','Bundled examples ({n} items)')}</option><option value="custom">${t('Custom path…')}</option></select><input id="benchmarkPath" type="text" aria-label="${t('Custom path…')}" placeholder="${t('/path/to/set.jsonl or a directory of .jsonl')}" hidden><button type="button" id="runBenchmark">${t('Run')}</button><button type="button" id="cancelBenchmark" hidden>${t('Cancel')}</button><div id="benchmarkProgress" class="muted job-progress"></div><a class="dashboard-link" href="#logs/job%3Abenchmark">${t('Logs')}</a></div>
<div class="bench-params" data-kinds="loadtest determinism"><div class="qf-row"><div class="qf"><label for="bpSynthetic">${t('Synthetic tokens')}</label><input type="number" id="bpSynthetic" min="50" step="50" placeholder="${t('use the set')}"></div><div class="qf" data-kinds="loadtest"><label for="bpNSeq">${t('Sequential')}</label><input type="number" id="bpNSeq" min="1" value="200"></div><div class="qf" data-kinds="loadtest"><label for="bpNConc">${t('Concurrent')}</label><input type="number" id="bpNConc" min="1" value="1000"></div><div class="qf"><label for="bpConcurrency">${t('Concurrency')}</label><input type="number" id="bpConcurrency" min="1" value="32"></div><div class="qf" data-kinds="loadtest"><label for="bpQpr">${t('Questions per request')}</label><input type="number" id="bpQpr" min="1" max="5" value="1"></div><div class="qf" data-kinds="loadtest"><label for="bpSeed">${t('Seed')}</label><input type="number" id="bpSeed" min="0" value="0"></div><div class="qf" data-kinds="loadtest"><label for="bpLong">${t('Long share')}</label><input type="number" id="bpLong" min="0" max="1" step="0.1" value="0"></div><div class="qf" data-kinds="determinism"><label for="bpN">${t('Requests (N)')}</label><input type="number" id="bpN" min="1" value="50"></div><div class="qf" data-kinds="loadtest" id="bpEarlyExitField" hidden><label for="bpEarlyExit">${t('Early exit')}</label><select id="bpEarlyExit"></select></div></div></div>
<div class="bench-params" data-kinds="modes"><div id="bpModes" class="controls" role="group" aria-label="${t('Compare engine modes')}"></div><div class="qf-row"><div class="qf"><label for="bpModesN">${t('Questions (n)')}</label><input type="number" id="bpModesN" min="10" value="100"></div><div class="qf"><label for="bpLatN">${t('Latency samples')}</label><input type="number" id="bpLatN" min="5" value="30"></div></div></div>
<p class="muted" id="benchmarkHint"></p><div id="benchmarkResults"></div>`;
$('#benchmarkSet').value=stored('basal-ui.benchmarkSet')==='custom'?'custom':'bundled';
$('#benchmarkPath').value=stored('basal-ui.benchmarkPath')||'';
$('#benchmarkKind').value=stored('basal-ui.benchmarkKind')||'benchmark';
$('#benchmarkSet').onchange=()=>{store('basal-ui.benchmarkSet',$('#benchmarkSet').value);updateBenchmarkForm();};
$('#benchmarkPath').oninput=()=>store('basal-ui.benchmarkPath',$('#benchmarkPath').value);
$('#benchmarkKind').onchange=()=>{store('basal-ui.benchmarkKind',$('#benchmarkKind').value);benchModelsSignature='';renderBenchmarkState(statusCache?.jobs?.benchmark,readyModels());};
$('#benchmarkModel').onchange=()=>{updateBenchmarkForm();if($('#benchmarkKind').value==='modes')loadComparisonModes();};
let benchModelsSignature='', comparisonFor='';
function updateBenchmarkForm() {
  const kind=$('#benchmarkKind').value;
  document.querySelectorAll('#benchmarks [data-kinds]').forEach(x=>x.hidden=!x.dataset.kinds.split(' ').includes(kind));
  $('#benchmarkSet').hidden=kind==='modes'; $('#benchmarkPath').hidden=kind==='modes'||$('#benchmarkSet').value!=='custom';
  const levels=modelEntry($('#benchmarkModel').value)?.early_exit||[];
  $('#bpEarlyExit').innerHTML=['',...levels].map(l=>`<option value="${esc(l)}">${l||'off'}</option>`).join('');
  $('#bpEarlyExitField').hidden=kind!=='loadtest'||!levels.length;
  $('#benchmarkHint').innerHTML=BENCH_HINTS[kind];
}
async function loadComparisonModes() {
  const id=$('#benchmarkModel').value;
  if(!id||id===comparisonFor)return;
  comparisonFor=id;
  try{
    const d=await api(`/api/modes?id=${encodeURIComponent(id)}`);
    const preset=new Set(d.modes.filter(m=>!['ollama','llamacpp'].includes(m)).slice(0,2));  // external services: opt in
    $('#bpModes').innerHTML=d.modes.map(m=>`<label><input type="checkbox" role="switch" data-mode="${esc(m)}"${preset.has(m)?' checked aria-checked="true"':' aria-checked="false"'}> ${esc(m)}</label>`).join('')||`<span class="muted">${t('No mode can run this model here.')}</span>`;
  }catch(e){comparisonFor='';$('#bpModes').innerHTML=`<span class="reason">${esc(e.message)}</span>`;}
}
const intOf=sel=>$(sel).value===''?undefined:Number($(sel).value);
$('#runBenchmark').onclick=()=>act(async()=>{
  const kind=$('#benchmarkKind').value, body={id:$('#benchmarkModel').value,kind};
  if(kind!=='modes')body.set=$('#benchmarkSet').value==='custom'?$('#benchmarkPath').value.trim():'bundled';
  if(kind==='loadtest')body.params={n_seq:intOf('#bpNSeq'),n_conc:intOf('#bpNConc'),concurrency:intOf('#bpConcurrency'),questions_per_request:intOf('#bpQpr'),seed:intOf('#bpSeed'),long_frac:intOf('#bpLong'),synthetic:intOf('#bpSynthetic')??null,early_exit:$('#bpEarlyExit').value||null};
  if(kind==='determinism')body.params={determinism:intOf('#bpN'),concurrency:intOf('#bpConcurrency'),synthetic:intOf('#bpSynthetic')??null};
  if(kind==='modes')Object.assign(body,{modes:[...document.querySelectorAll('#bpModes [data-mode]:checked')].map(x=>x.dataset.mode),n:intOf('#bpModesN'),lat_n:intOf('#bpLatN')});
  if(body.params)body.params=Object.fromEntries(Object.entries(body.params).filter(([,v])=>v!==undefined));
  await api('/api/benchmark',{method:'POST',body:JSON.stringify(body)});
  toast(t('{benchmark} started',{benchmark:$('#benchmarkKind').selectedOptions[0].textContent})); await refreshStatus();
});
$('#cancelBenchmark').onclick=()=>act(async()=>{await api('/api/benchmark',{method:'DELETE'});await refreshStatus();});
function renderBenchmarkState(bench, ready) {
  const kind=$('#benchmarkKind').value, select=$('#benchmarkModel'), current=select.value;
  const ids=kind==='modes'?modelData.filter(m=>m.downloaded).map(m=>m.id):ready.map(m=>m.id);
  const signature=JSON.stringify([kind,ids]);
  if(signature!==benchModelsSignature){
    benchModelsSignature=signature;
    select.innerHTML=ids.map(id=>`<option value="${esc(id)}">${esc(id)}</option>`).join('')||`<option value="">${kind==='modes'?t('Download a model first'):t('Load a model first')}</option>`;
    select.disabled=!ids.length; if(ids.includes(current))select.value=current;
    updateBenchmarkForm(); if(kind==='modes'){comparisonFor='';loadComparisonModes();}
  }
  const running=Boolean(bench?.running);
  $('#runBenchmark').disabled=running||!ids.length; $('#cancelBenchmark').hidden=!running;
  $('#benchmarkProgress').innerHTML=running&&bench.total?progressBar(bench.done,bench.total,t('Benchmarks'),`${bench.done} / ${bench.total}`,'progress bench-progress'):'';
  $('#benchmarkProgress').prepend(!bench?(ids.length?'':kind==='modes'?t('Download a model to compare its modes.'):t('Load a model to benchmark it.')):running?`${shortName(bench.model)} · ${bench.label} · ${ts(bench.phase,bench.phase_code,bench.phase_params)} ${bench.done}/${bench.total}`:bench.phase==='error'?`${t('Failed: {error}',{error:bench.error})}`:bench.phase==='cancelled'?t('Cancelled'):'');
  if(benchRunning&&!running)loadStats();
  benchRunning=running;
}
function benchmarkRow(x) {
  const kind=x.kind||'benchmark', head=`<td title="${esc(x.model)}">${esc(shortName(x.model))}</td><td><span class="chip">${esc(kind)}</span></td><td title="${esc(t(x.set))}">${esc(x.set==='bundled'?t('bundled'):shortName(x.set))}${x.items!=null?` (${x.items})`:''}</td><td>${esc(new Date(x.when*1000).toLocaleString())}</td>`;
  if(kind==='determinism'){const d=x.determinism||{};return `<tr>${head}<td colspan="7"><span class="badge ${d.identical===d.requests?'fit-fits':'fit-tight'}">${t('identical')} ${esc(d.identical)}/${esc(d.requests)}</span> ${t('max |Δp|')} ${esc(d.max_abs_diff)} · ${t('decision flips')} ${esc(d.decision_flips)} · ${t('concurrency')} ${esc(d.concurrency)}</td></tr>`;}
  if(kind==='modes'){
    const f=(v,d=1)=>v==null||Number.isNaN(Number(v))?'—':Number(v).toFixed(d);
    const rows=(x.modes||[]).map(m=>m.error?`<tr><td>${esc(m.mode)}</td><td colspan="8"><span class="reason wide">${esc(m.error)}</span></td></tr>`:`<tr><td>${esc(m.mode)}</td><td>${f(m.lat1_ms)}</td><td>${f(m.lat2_ms)}</td><td>${f(m.lat2_p95_ms)}</td><td>${f(m.dec_s)}</td><td>${m.agree==null?`ref`:f(m.agree,3)}</td><td>${m.acc==null?'—':pct(m.acc)}</td><td>${f(m.load_s)}</td><td>${f(m.mem_gb,2)}</td></tr>`).join('');
    return `<tr>${head}<td colspan="7"><details><summary>${(x.modes||[]).map(m=>esc(m.mode)).join(' vs ')}</summary><table class="mini-table"><thead><tr><th>${t('Mode')}</th><th>${t('lat1 ms')}</th><th>${t('lat2 ms')}</th><th>${t('p95 ms')}</th><th>${t('dec/s')}</th><th>${t('agree')}</th><th>${t('acc')}</th><th>${t('load s')}</th><th>${t('mem GB')}</th></tr></thead><tbody>${rows}</tbody></table></details></td></tr>`;
  }
  return `<tr>${head}<td>${num(x.p50_ms,0)} ms</td><td>${num(x.p95_ms,0)} ms</td><td>${num(x.decisions_per_s)}</td><td>${num(x.questions_per_s)}</td><td>${num(x.input_tokens_per_s,0)}</td><td title="${esc(Object.entries(x.accuracy?.by_file||{}).map(([f,v])=>`${f}: ${pct(v)}`).join(', '))}">${pct(x.accuracy?.overall)}</td><td>${esc(x.errors??'—')}</td></tr>`;
}
// per model, the newest preset benchmark: accuracy by file and latency as bars, and the trend over its kept runs
function benchmarkCharts() {
  const ms=v=>`${num(v,0)} ms`, when=x=>new Date(x.when*1000).toLocaleString();
  return Object.entries(statsData.benchmarks||{}).map(([id,runs])=>{
    const preset=runs.filter(x=>(x.kind||'benchmark')==='benchmark').sort((a,b)=>a.when-b.when), last=preset.at(-1);
    if(!last)return '';
    const files=Object.entries(last.accuracy?.by_file||{}).map(([file,v])=>({label:file,value:v}));
    const latency=[['p50',last.p50_ms],[t('mean'),last.mean_ms],['p95',last.p95_ms]].map(([label,value],i)=>({label,value,series:i?3:0}));
    const trend=(label,key,format,series)=>preset.length>1?`<div class="stat"><span class="stat-label">${label}</span>${chartSparkline({label:`${label} · ${shortName(id)}`,points:preset.map(x=>({x:when(x),y:key(x)})),format,series})}</div>`:'';
    return `<article class="bench-model"><div class="section-head"><h4 title="${esc(id)}">${esc(shortName(id))}</h4><span class="muted">${t('Newest benchmark')} · ${esc(last.set==='bundled'?t('bundled'):shortName(last.set))} · ${esc(when(last))}</span></div><div class="bench-charts">${files.length?`<div><h5 class="viz-title">${t('Accuracy {value}',{value:pct(last.accuracy?.overall)})}</h5>${chartBars({label:t('Accuracy by file, {model}',{model:shortName(id)}),items:files,max:1,format:pct})}</div>`:''}<div><h5 class="viz-title">${t('Latency')}</h5>${chartBars({label:t('Latency of the newest benchmark, {model}',{model:shortName(id)}),items:latency,format:ms})}</div></div>${preset.length>1?`<div class="stats bench-trend">${trend(t('Accuracy over runs'),x=>x.accuracy?.overall,pct,0)}${trend(t('p50 latency over runs'),x=>x.p50_ms,ms,1)}${trend(t('Decisions per second over runs'),x=>x.decisions_per_s,v=>num(v),2)}</div>`:''}</article>`;
  }).join('');
}
function renderBenchmarkResults() {
  const rows=Object.values(statsData.benchmarks||{}).flat().sort((a,b)=>b.when-a.when);
  $('#benchmarkResults').innerHTML=rows.length?benchmarkCharts()+`<div class="table-wrap" tabindex="0" role="region" aria-label="${t('Benchmarks')}"><table><thead><tr><th>${t('Model')}</th><th>${t('Kind')}</th><th>${t('Set')}</th><th>${t('When')}</th><th>p50</th><th>p95</th><th>${t('dec/s')}</th><th>q/s</th><th>tok/s</th><th>${t('Accuracy')}</th><th>${t('Errors')}</th></tr></thead><tbody>${rows.map(benchmarkRow).join('')}</tbody></table></div>`:`<div class="empty-state"><strong>${t('No benchmark results yet.')}</strong><span>${t('Load a model, pick a measurement above and press Run.')}</span></div>`;
}
// Calibration: basal-calibrate on the user's labelled requests (a temporary uncalibrated child of the model; with task
// questions the engine in basal-ui's process), or a file fitted elsewhere imported
$('#calibrationBlock').innerHTML=`<div class="section-head"><h3>${t('Calibration')}</h3></div><div class="controls"><select id="calibrationModel" aria-label="${t('Model to calibrate')}"></select><label class="calibration-path">${t('Labelled requests')} <input class="input" id="calibrationInput" type="text" placeholder="${t('/path/to/labelled.jsonl')}"></label><fieldset class="target-group"><legend>${t('Error-rate targets')}</legend><label><input id="calibrationT1" class="check" type="checkbox" checked> 1%</label><label><input id="calibrationT5" class="check" type="checkbox" checked> 5%</label></fieldset><button type="button" id="runCalibration">${t('Calibrate')}</button><button type="button" id="cancelCalibration" hidden>${t('Cancel')}</button><div id="calibrationProgress" class="muted job-progress"></div><a class="dashboard-link" href="#logs/job%3Acalibrate">${t('Logs')}</a></div><div class="controls"><label class="calibration-path">${t('Calibration file to import')} <input class="input" id="calibrationImportPath" type="text" placeholder="/path/to/CALIBRATION.custom.json"></label><button type="button" id="importCalibration">${t('Import')}</button></div><p class="muted calibration-intro">${t('Fits temperatures and certified thresholds on your labelled requests, or imports a file fitted elsewhere. The selected file applies from the model\'s next start.')}</p><details class="calibration-help"><summary>${t('Learn more')}</summary><p class="muted">${t("Fits temperatures and certified thresholds on your labelled requests (format: {input}; the simple {run} items with {gold} work too). Decision questions run against a temporary uncalibrated copy of the model, which needs its memory once more while it runs; task questions (classify / verify / extract with gold) are certified on the answers as served, so the engine runs in basal-ui's process instead (cancelling then takes effect when basal-calibrate finishes). Import adds a file fitted elsewhere (basal-calibrate on another machine); one fitted on another model is listed with basal-serve's warning. The selected file applies from the model's next start.",{input:'<code>docs/CALIBRATION.md#input</code>',run:'<code>basal-run</code>',gold:'<code>gold</code>'})}</p></details><div id="calibrationFiles"></div><dialog id="reportDialog" class="confirm-dialog report-dialog" aria-labelledby="reportTitle"><h2 id="reportTitle">${t('Calibration report')}</h2><pre id="reportText" class="logs"></pre><div class="dialog-actions"><button type="button" id="reportClose">${t('Close')}</button></div></dialog>`;
$('#calibrationBlock').insertAdjacentHTML('beforeend',`<details id="calibrationAdvanced"><summary>${t('Advanced')}</summary><div class="controls"><label><input type="checkbox" id="calStrict"> ${t('Strict')}</label><label>${t('Reference')} <input type="text" id="calReference" placeholder="${t('basal-1.5 or path')}"></label><label>${t('Min/type')} <input type="number" id="calMinType" min="1" value="1"></label><label>${t('Min blend')} <input type="number" id="calMinBlend" min="0" value="0"></label><label>${t('Min family')} <input type="number" id="calMinFamily" min="1" value="1"></label><label>${t('Min/set')} <input type="number" id="calMinSet" min="1" value="1"></label><label>${t('Concurrency')} <input type="number" id="calConcurrency" min="1" max="256" value="1"></label><label>${t('Compare')} <select id="calCompare"><option value="shipped">${t('shipped')}</option><option value="none">${t('none')}</option><option value="path">${t('calibration file…')}</option></select></label><input id="calComparePath" type="text" placeholder="${t('calibration file path')}" hidden><label><input type="checkbox" id="calSaveRows"> ${t('Save rows')}</label><label><input type="checkbox" id="calReportOnly"> ${t('Report only')}</label></div></details>`);
$('#calCompare').onchange=()=>$('#calComparePath').hidden=$('#calCompare').value!=='path';
['calStrict','calSaveRows','calReportOnly'].forEach(id=>{const x=$('#'+id);x.setAttribute('role','switch');setChecked(x,x.checked);});
$('#calibrationInput').value=stored('basal-ui.calibrationInput')||'';
$('#calibrationInput').oninput=()=>store('basal-ui.calibrationInput',$('#calibrationInput').value);
document.querySelectorAll('#calibrationBlock input[type=checkbox]:not(.check)').forEach(i=>{i.setAttribute('role','switch');setChecked(i,i.checked);});
document.body.append($('#reportDialog'));  // a modal inside a hidden view would block the page without showing
$('#reportClose').onclick=()=>$('#reportDialog').close();
let calibrationRunning=false, calibrationSignature='', calibrationReportSeen=null, calibrationSequence=0;
function renderCalibrationModels() {
  const select=$('#calibrationModel'), current=select.value||stored('basal-ui.calibrationModel')||'', rows=modelData.filter(m=>m.downloaded);
  const signature=JSON.stringify(rows.map(m=>m.id));
  if(signature!==calibrationSignature){
    calibrationSignature=signature;
    select.innerHTML=rows.map(m=>`<option value="${esc(m.id)}">${esc(m.id)}</option>`).join('')||`<option value="">${t('Download a model first')}</option>`;
    select.disabled=!rows.length; if(rows.some(m=>m.id===current))select.value=current;
    loadCalibrations();
  }
  $('#runCalibration').disabled=calibrationRunning||!rows.length;
}
$('#calibrationModel').onchange=()=>{store('basal-ui.calibrationModel',$('#calibrationModel').value);loadCalibrations();};
$('#runCalibration').onclick=()=>act(async()=>{
  const targets=[['#calibrationT1','0.01'],['#calibrationT5','0.05']].filter(([s])=>$(s).checked).map(([,t])=>t);
  await api('/api/calibrate',{method:'POST',body:JSON.stringify({id:$('#calibrationModel').value,input:$('#calibrationInput').value.trim(),targets})});
  toast(t('Calibration started')); await refreshStatus();
});
$('#cancelCalibration').onclick=()=>act(async()=>{await api('/api/calibrate',{method:'DELETE'});await refreshStatus();});
$('#runCalibration').onclick=()=>act(async()=>{
  const targets=[['#calibrationT1','0.01'],['#calibrationT5','0.05']].filter(([s])=>$(s).checked).map(([,t])=>t);
  const compare=$('#calCompare').value,advanced={strict:$('#calStrict').checked,reference:$('#calReference').value.trim()||null,min_per_type:Number($('#calMinType').value),min_blend:Number($('#calMinBlend').value),min_family:Number($('#calMinFamily').value),min_per_set:Number($('#calMinSet').value),concurrency:Number($('#calConcurrency').value),compare:compare==='path'?$('#calComparePath').value.trim():compare,save_rows:$('#calSaveRows').checked,report_only:$('#calReportOnly').checked};
  await api('/api/calibrate',{method:'POST',body:JSON.stringify({id:$('#calibrationModel').value,input:$('#calibrationInput').value.trim(),targets,advanced})});
  toast(t('Calibration started')); await refreshStatus();
});
$('#importCalibration').onclick=()=>act(async()=>{
  const id=$('#calibrationModel').value, sequence=++calibrationSequence;
  const d=await api('/api/calibrations/import',{method:'POST',body:JSON.stringify({id,path:$('#calibrationImportPath').value.trim()})});
  if(!calibrationCurrent(id,sequence))return;
  renderCalibrationFiles(id,d); $('#calibrationImportPath').value=''; $('#calibrationImportPath').dispatchEvent(new Event('pathcheck'));
  const f=d.files.find(x=>x.file===d.file); toast(f?.warning?`${t('Imported')} — ${f.warning}`:t('Calibration imported'));
});
// the certified thresholds {target: {type: threshold}} with the task ones {task: {target: threshold}}
function allThresholds(f) {
  const out=JSON.parse(JSON.stringify(f?.thresholds||{}));
  for(const [task,byTarget] of Object.entries(f?.task_thresholds||{}))for(const [k,v] of Object.entries(byTarget||{}))(out[k]??={})[task]=v;
  return out;
}
function renderCalibrationState(job) {
  const running=Boolean(job?.running);
  $('#cancelCalibration').hidden=!running; $('#runCalibration').disabled=running||!$('#calibrationModel').value;
  $('#calibrationProgress').innerHTML=running?progressBar(0,0,t('Calibration'),ts(job.phase,job.phase_code,job.phase_params),'progress bench-progress'):'';
  $('#calibrationProgress').prepend(!job?'':running?`${shortName(job.model)} · ${ts(job.phase,job.phase_code,job.phase_params)}…`:job.phase==='error'?`${t('Failed: {error}',{error:job.error})}`:job.phase==='cancelled'?t('Cancelled'):['done','done_with_warnings'].includes(job.phase)?`${job.phase==='done_with_warnings'?t('Completed with warnings'):t('Completed (see report for certification)')}: ${shortName(job.result?.file||'')}${job.result?.warnings?.length?' · '+job.result.warnings.join(' · '):''}`:'');
  if(calibrationRunning&&!running)loadCalibrations();
  if(!running&&job?.result?.report_only&&calibrationReportSeen!==job.started){calibrationReportSeen=job.started;$('#reportText').textContent=job.result.report||'';$('#reportDialog').showModal();}
  calibrationRunning=running;
}
async function loadCalibrations() {
  const id=$('#calibrationModel').value, sequence=++calibrationSequence, files=$('#calibrationFiles');
  files.innerHTML='';
  files.setAttribute('aria-busy','true');
  $('#importCalibration').disabled=true;
  if(!id){files.removeAttribute('aria-busy');return;}
  try{
    const data=await api(`/api/calibrations?id=${encodeURIComponent(id)}`);
    if(!calibrationCurrent(id,sequence))return;
    renderCalibrationFiles(id,data);
  }catch(e){
    if(!calibrationCurrent(id,sequence))return;
    files.innerHTML=`<div class="error-box">${esc(e.message)}</div>`;
  }finally{
    if(calibrationCurrent(id,sequence)){files.removeAttribute('aria-busy');$('#importCalibration').disabled=false;}
  }
}
function calibrationCurrent(id,sequence){return sequence===calibrationSequence&&id===$('#calibrationModel').value;}
function thresholdCell(thresholds) {
  const types=[...new Set(Object.values(thresholds||{}).flatMap(Object.keys))];
  return types.map(t=>`<span class="chip">${esc(t)} ${['0.01','0.05'].map(k=>`${(Number(k)*100).toFixed(0)}%: ${thresholds?.[k]?.[t]==null?'—':Number(thresholds[k][t]).toFixed(3)}`).join(' · ')}</span>`).join(' ')||`<span class="muted">${t('none certified')}</span>`;
}
// the calibration in use (selected, else shipped, else the newest file) drawn: certified thresholds per type, the share
// automated at each target and the leave-one-family-out ECE per type against its reference (no reliability bins: the
// calibration file does not keep them)
function calibrationCharts(d) {
  const f=d.files.find(x=>x.file===d.selected)||(d.shipped&&!d.shipped.error?d.shipped:null)||d.files.at(-1);
  if(!f)return '';
  const name=f===d.shipped?t('Shipped default'):shortName(f.file), th=allThresholds(f);
  const rows=[...new Set(Object.values(th).flatMap(Object.keys))].map(type=>({label:type,one:th['0.01']?.[type],five:th['0.05']?.[type]}));
  const gates=f.gates||{}, auto=Object.entries(gates.automated_at||{}).map(([k,v])=>({label:t('{target} error target',{target:pct(k)}),value:v,note:gates.automated_error?.[k]==null?'':t('{error} wrong',{error:pct(gates.automated_error[k])})}));
  const ece=Object.entries(gates.ece_lofo||{}).flatMap(([type,e])=>e?.mean==null?[]:[{label:type,value:e.mean,series:(e.regression??(e.reference!=null&&e.mean>e.reference))?1:0},...(e.reference==null?[]:[{label:`${type} · ${t('reference')}`,value:e.reference,series:3}])]);
  const parts=[rows.length&&`<div><h4 class="viz-title">${t('Certified thresholds')}</h4>${chartThresholds({label:t('Certified confidence thresholds per question type, {file}',{file:name}),rows})}</div>`,
    auto.length&&`<div><h4 class="viz-title">${t('Automated on CERT')}</h4>${chartBars({label:t('Share of CERT decisions automated at each error target, {file}',{file:name}),items:auto,max:1,format:pct})}</div>`,
    ece.length&&`<div><h4 class="viz-title">${t('Leave-one-family-out ECE')}</h4>${chartBars({label:t('Mean leave-one-family-out ECE per type against its reference, {file}',{file:name}),items:ece,format:v=>num(v,3)})}</div>`].filter(Boolean);
  return parts.length?`<div class="calibration-charts"><p class="muted">${t('Drawn: {file}',{file:name})}</p><div class="bench-charts">${parts.join('')}</div></div>`:'';
}
function renderCalibrationFiles(id, d) {
  const loaded=(statusCache?.models||[]).find(m=>m.id===id);
  const row=(f,value,label)=>{
    const fitted=f?.fitted_on&&typeof f.fitted_on==='object'?f.fitted_on:null, gates=f?.gates||{};
    const automated=Object.entries(gates.automated_at||{}).map(([k,v])=>`${(Number(k)*100).toFixed(0)}%: ${pct(v)} at ${pct(gates.automated_error?.[k])} error`).join(' · ');
    const checked=(d.selected||'')===value;
    return `<tr><td><label><input type="radio" name="calibrationUse" value="${esc(value)}"${checked?' checked':''}> ${esc(label)}</label>${f?.imported?` <span class="chip">${t('imported')}</span>`:''}${checked&&loaded?.restart_required?` <span class="badge fit-tight">${t('Restart to apply')}</span>`:''}${f?.warning?`<span class="reason warning">${esc(f.warning)}</span>`:''}${f?.error?`<span class="reason calibration-error">${t('This file cannot be read.')} <small>${esc(f.error)}</small></span>`:''}${(f?.warnings||[]).map(w=>`<span class="reason warning">${esc(w)}</span>`).join('')}</td><td>${fitted?`${esc(fitted.data??'')} · ${esc(fitted.decisions??'—')} ${t('decisions')}`:esc(typeof f?.fitted_on==='string'?f.fitted_on:'—')}</td><td>${fitted?Object.entries(fitted.per_type||{}).map(([t,n])=>`<span class="chip">${esc(t)} ${esc(n)}</span>`).join(' '):''}</td><td>${thresholdCell(allThresholds(f))}</td><td>${esc(automated||'—')}</td><td>${value&&f?.has_report?`<button type="button" data-report="${esc(value)}">${t('Report')}</button>`:''}</td></tr>`;
  };
  const ctx=d.shipped?.context||{}, context=d.shipped?`${t('Shipped default')}: ${esc(shortName(d.shipped.file))} · ${esc(ctx.active?t('running settings'):t('saved settings for next start'))} · ${esc(ctx.mode)}${ctx.quant?` · ${esc(ctx.quant)}`:''}${ctx.preset?` · ${esc(ctx.preset)}`:''} · ${esc(ctx.layout)}${ctx.adapter?` · adapter ${esc(shortName(ctx.adapter))}`:''}`:t('Shipped default unavailable');
  const selection=d.selected?`${t('Selected custom calibration for next start')}: ${esc(shortName(d.selected))}`:`${t('Selected calibration')}: ${t('shipped default')}`;
  const shipped=d.shipped?row(d.shipped,'',`${t('Shipped default')} · ${shortName(d.shipped.file)}`):'';
  $('#calibrationFiles').innerHTML=`<p class="muted">${context}<br>${selection}</p><div class="table-wrap"><table><thead><tr><th>${t('Use')}</th><th>${t('Fitted on')}</th><th>${t('Per type')}</th><th>${t('Certified thresholds')}</th><th>${t('Automated on CERT')}</th><th><span class="sr-only">${t('Action')}</span></th></tr></thead><tbody>${shipped}${d.files.map(f=>row(f,f.file,new Date(f.when*1000).toLocaleString())).join('')}</tbody></table></div>${calibrationCharts(d)}`;
  for(const f of d.files||[])if(f.rows){
    const radio=[...$('#calibrationFiles').querySelectorAll('input[name=calibrationUse]')].find(x=>x.value===f.file);
    if(!radio)continue;
    const button=document.createElement('button');button.type='button';button.textContent=t('Refit');button.dataset.refit=f.rows;
    radio.closest('tr').lastElementChild.append(' ',button);
  }
  $('#calibrationFiles').querySelectorAll('[data-refit]').forEach(b=>b.onclick=()=>act(async()=>{
    if(id!==$('#calibrationModel').value)return;
    await api('/api/calibrate',{method:'POST',body:JSON.stringify({id,input:b.dataset.refit,from_rows:true})});
    if(id!==$('#calibrationModel').value)return;
    toast(t('Calibration refit started'));await refreshStatus();
  }));
  $('#calibrationFiles').querySelectorAll('input[name=calibrationUse]').forEach(r=>r.onchange=()=>act(async()=>{
    if(id!==$('#calibrationModel').value)return;
    const sequence=++calibrationSequence, data=await api('/api/calibrations/select',{method:'POST',body:JSON.stringify({id,file:r.value||null})});
    if(!calibrationCurrent(id,sequence))return;
    renderCalibrationFiles(id,data);
    toast(loaded?t('Calibration selected — restart the model to apply'):t('Calibration selected')); await refreshStatus();
  }));
  $('#calibrationFiles').querySelectorAll('[data-report]').forEach(b=>b.onclick=()=>act(async()=>{
    if(id!==$('#calibrationModel').value)return;
    const sequence=++calibrationSequence, r=await api(`/api/calibrations/report?id=${encodeURIComponent(id)}&file=${encodeURIComponent(b.dataset.report)}`);
    if(!calibrationCurrent(id,sequence))return;
    $('#reportText').textContent=r.report; $('#reportDialog').showModal();
  }));
}
