// Batch view.
'use strict';
// Batch: basal-run over a JSONL file against a loaded model (basal_ui/batch.py)
function answerBest(a) {
  if(!a||typeof a!=='object')return null;
  for(const key of ['choice','action','tool','label','score'])if(a[key]!=null)return String(typeof a[key]==='number'?Number(a[key]).toFixed(2):a[key]);
  for(const key of ['selected','labels','tools'])if(Array.isArray(a[key]))return a[key].join(', ')||t('none');
  if(typeof a.noul==='number')return a.noul>=0.5?'true':'false';
  return a.type||null;
}
let batchRowsLoaded=0, batchRunning=false, batchResult=null;
$('#batchInput').value=localStorage.getItem('basal-ui.batchInput')||'';
$('#batchInput').oninput=()=>localStorage.setItem('basal-ui.batchInput',$('#batchInput').value);
function renderBatchState(job, ready) {
  const select=$('#batchModel'), current=select.value, ids=ready.map(m=>m.id);
  if(JSON.stringify([...select.options].map(o=>o.value))!==JSON.stringify(ids))select.innerHTML=ids.map(id=>`<option value="${esc(id)}">${esc(id)}</option>`).join('')||`<option value="">${t('Load a model first')}</option>`;
  if(ids.includes(current))select.value=current; else if(ids.includes(selectedModel))select.value=selectedModel;
  const levels=modelEntry(select.value)?.early_exit||[], ee=$('#batchEarlyExit');
  if(JSON.stringify([...ee.options].map(o=>o.value))!==JSON.stringify(['',...levels]))ee.innerHTML=['',...levels].map(l=>`<option value="${esc(l)}">${l?`${t('early exit')} ${esc(l)}`:t('no early exit')}</option>`).join('');
  ee.hidden=ee.parentElement.hidden=!levels.length;
  const running=Boolean(job?.running);
  $('#runBatch').disabled=running||!ids.length; $('#cancelBatch').hidden=!running;
  $('#batchProgress').innerHTML=!job?'':running?`<div class="progress-wrap">${progressBar(job.done,job.total,t('Batch'),`${job.done} / ${job.total}`)}<small aria-hidden="true">${job.done} / ${job.total}</small></div>`:job.phase==='error'?`<div class="error-box">${esc(job.error)}</div>`:job.phase==='cancelled'?`<p class="muted">${t('Cancelled.')}</p>`:'';
  if(!running&&job?.phase==='done'&&(batchRunning||!batchResult))showBatchResult(job.result);
  batchRunning=running;
}
function showBatchResult(r) {
  batchResult=r;
  $('#batchSummary').innerHTML=`<div class="chips"><span class="chip">${tp(r.items,'{n} items','{n} items')}</span><span class="chip">${tp(r.errors,'{n} errors','{n} errors')}</span><span class="chip">${esc(r.seconds)} s</span><span class="chip">${esc(r.items_per_s)} ${t('items/s')}</span><span class="chip">${t('server p50')} ${esc(r.server_latency_p50_ms??'—')} ms</span>${r.accuracy!=null?`<span class="chip">${t('accuracy')} ${pct(r.accuracy)}</span>`:''}</div><p class="muted mono">${esc(r.output)}</p>`;
  $('#downloadBatch').hidden=$('#copyBatchPath').hidden=false;
  batchRowsLoaded=0; $('#batchRows').innerHTML=''; loadBatchRows(); batchReliability(r).catch(()=>{});
}
// labelled rows (a gold answer): the reliability diagram of their confidence against correct, from up to 1000 rows
async function batchReliability(r) {
  if(r.accuracy==null)return;
  const d=await api('/api/batch/rows?offset=0&limit=1000');
  if(batchResult!==r)return;
  const pairs=d.rows.filter(x=>typeof x.correct==='boolean'&&finite(x.confidence)).map(x=>[Number(x.confidence),x.correct]);
  if(pairs.length<10)return;
  $('#batchSummary').insertAdjacentHTML('beforeend',`<div class="batch-reliability"><h3 class="viz-title">${t('Reliability')}${d.total>d.rows.length?` · ${t('first {n} rows',{n:d.rows.length})}`:''}</h3>${chartReliability({label:t('Reliability diagram of the batch answers: confidence against the share correct'),pairs})}</div>`);
}
async function loadBatchRows() {
  const d=await api(`/api/batch/rows?offset=${batchRowsLoaded}&limit=100`);
  const rows=d.rows.map(x=>`<tr><td>${esc(x.id)}</td><td>${x.option!=null?`<span class="chip">${esc(x.prediction)}: ${esc(x.option)} (${pct(x.confidence)})</span>`:Object.entries(x.answers||{}).map(([n,a])=>`<span class="chip">${esc(n)}: ${esc(answerBest(a)??'—')}${a?.confidence!=null?` (${pct(a.confidence)})`:''}</span>`).join(' ')}${x.correct===true?` <span class="badge fit-fits">${t('correct')}</span>`:x.correct===false?` <span class="badge fit-too_large">${t('wrong')}</span>`:''}</td><td>${num(x.latency_ms,1)} ms</td><td>${x.error?`<span class="reason">${esc(httpFailure(x.status,textOf(x.error)))}${x.request_id?` [${t('request')} ${esc(x.request_id)}]`:""}</span>`:''}</td></tr>`).join('');
  if(!batchRowsLoaded)$('#batchRows').innerHTML=`<table><thead><tr><th>${t('Id')}</th><th>${t('Answer')}</th><th>${t('Latency')}</th><th>${t('Error')}</th></tr></thead><tbody></tbody></table>`;
  $('#batchRows tbody').insertAdjacentHTML('beforeend',rows);
  batchRowsLoaded+=d.rows.length; $('#batchMore').hidden=batchRowsLoaded>=d.total;
}
$('#batchMore').onclick=()=>act(loadBatchRows);
$('#runBatch').onclick=()=>act(async()=>{
  const body={id:$('#batchModel').value,input:$('#batchInput').value.trim(),concurrency:Number($('#batchConcurrency').value)||16};
  if($('#batchOutput').value.trim())body.output=$('#batchOutput').value.trim();
  if(!$('#batchEarlyExit').hidden&&$('#batchEarlyExit').value)body.early_exit=$('#batchEarlyExit').value;
  await api('/api/batch',{method:'POST',body:JSON.stringify(body)});
  $('#batchSummary').innerHTML=''; $('#batchRows').innerHTML=''; $('#batchMore').hidden=$('#downloadBatch').hidden=$('#copyBatchPath').hidden=true;
  toast(t('Batch started')); await refreshStatus();
});
$('#cancelBatch').onclick=()=>act(async()=>{await api('/api/batch',{method:'DELETE'});await refreshStatus();});
$('#downloadBatch').onclick=()=>{const a=document.createElement('a');a.href='/api/batch/download';a.click();};
$('#copyBatchPath').onclick=()=>copyText(batchResult?.output||'',t('Output path copied'));
