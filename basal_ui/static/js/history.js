// History view.
'use strict';
function syncHistoryModels(models) {
  const select=$('#historyModel'), current=select.value, ids=[...new Set([...models,...(current?[current]:[])])];
  if(JSON.stringify([...select.options].map(o=>o.value).slice(1))===JSON.stringify(ids))return;
  select.innerHTML=`<option value="">${t('All models')}</option>`+ids.map(id=>`<option value="${esc(id)}">${esc(id)}</option>`).join('');
  select.value=current;
}
let historySequence=0;
async function loadHistory(mode='reset') {
  clearTimeout(historyTimer);
  if($('#view-history').hidden)return;
  const sequence=++historySequence;
  const params=new URLSearchParams({limit:'100'}), query=$('#historySearch').value.trim();
  if($('#historyModel').value)params.set('model',$('#historyModel').value);
  if(historySource)params.set('source',historySource);
  if(query)params.set('q',query);
  if(mode==='more'&&historyBefore)params.set('before',historyBefore);
  if(mode==='poll')params.set('order','asc');
  if(mode==='poll'&&historyAfter)params.set('after',historyAfter);
  try{
    let d, items=[], pollCursor=null;
    do{
      d=await api(`/api/history?${params}`);
      const page=d.items||[]; items.push(...page);
      if(mode==='poll'&&page.length)pollCursor=page.at(-1).id;
      if(mode!=='poll'||page.length<100)break;
      params.set('after',page.at(-1).id);
    }while(sequence===historySequence);
    if(sequence!==historySequence)return;  // the filter changed meanwhile: that query's own fetch renders
    syncHistoryModels(d.models||[]);
    if(mode==='reset'){historyRows=new Map();historyOpen.clear();historyBrowsingOlder=false;}
    if(mode==='more')historyBrowsingOlder=true;
    items.forEach(x=>historyRows.set(x.id,x));
    if(mode!=='poll')$('#historyMore').hidden=items.length<100;
    let sorted=[...historyRows.values()].sort((a,b)=>b.ts-a.ts);
    if(mode==='reset'){historyAfter=items[0]?.id||null;historyBefore=items.at(-1)?.id||null;}
    else if(mode==='more'&&items.length)historyBefore=items.at(-1).id;
    else if(mode==='poll'){historyAfter=pollCursor||historyAfter;if(!historyBefore)historyBefore=items[0]?.id||null;}
    let trimmed=false;
    if(mode==='poll'&&!historyBrowsingOlder&&sorted.length>HISTORY_LIVE_MAX){
      sorted=sorted.slice(0,HISTORY_LIVE_MAX);
      historyRows=new Map(sorted.map(x=>[x.id,x]));
      [...historyOpen.keys()].forEach(id=>{if(!historyRows.has(id))historyOpen.delete(id);});
      historyBefore=sorted.at(-1).id; $('#historyMore').hidden=false;
      trimmed=true;
    }
    if(mode!=='poll'||items.length||trimmed||!$('#historyTable table'))renderHistory(sorted);
  }catch(e){if(sequence!==historySequence)return;$('#historyTable').innerHTML=`<div class="error-box">${esc(e.message)}</div>`;}
  if(!$('#view-history').hidden&&sequence===historySequence)historyTimer=setTimeout(()=>loadHistory('poll'),5000);
}
function renderHistory(items) {
  const flags=x=>`${x.deferred?` <span class="badge fit-too_large">${t('deferred')}</span>`:''}${x.truncated?` <span class="badge fit-tight">${t('truncated')}</span>`:''}`;
  // an answer without a best option or probability (verify, compress, extract…) shows its type; a cut state ends in …
  const answer=(n,a)=>a.best==null?`${n} · ${a.type||'?'}`:`${n}: ${a.best}${a.confidence==null?'':` (${pct(a.confidence)})`}`;
  const state=text=>String(text||'').length>=160?`${text}…`:text||'';  // history.PREVIEW characters at most
  const focused=$('#historyTable').contains(document.activeElement)?document.activeElement.closest('[data-history]')?.dataset.history:null;
  $('#historyTable').innerHTML=items.length?`<table class="history-table"><thead><tr><th>${t('Time')}</th><th>${t('Model')}</th><th>${t('Source')}</th><th>${t('State')}</th><th>${t('Answers')}</th><th>${t('Latency')}</th><th>${t('Status')}</th></tr></thead><tbody>${items.map(x=>`<tr class="history-row" data-history="${esc(x.id)}"><td><button type="button" class="history-open" aria-expanded="${historyOpen.has(x.id)}" title="${esc(new Date(x.ts*1000).toISOString())}">${esc(clock(x.ts))}</button></td><td class="history-model" title="${esc(x.model)}">${esc(shortName(x.model))}</td><td class="history-source"><span class="chip">${esc(t(x.source))}</span></td><td class="muted history-state">${esc(state(x.state_preview))}</td><td class="history-answers">${Object.entries(x.answers||{}).map(([n,a])=>`<span class="chip">${esc(answer(n,a))}</span>`).join(' ')}${flags(x)}</td><td class="history-latency">${num(x.latency_ms,0)} ms</td><td class="history-status"><span class="badge ${x.status>=200&&x.status<300?'fit-fits':'fit-too_large'}">${esc(x.status)}</span></td></tr>`).join('')}</tbody></table>`:`<div class="empty">${t('No decisions yet — try the Playground.')}</div>`;
  $('#historyTable').querySelectorAll('[data-history]').forEach(row=>{
    row.onclick=()=>act(()=>toggleHistory(row));  // pointer users click anywhere on the row
    const open=$('.history-open',row);
    open.onclick=e=>{e.stopPropagation();act(()=>toggleHistory(row));};  // keyboard: Tab to the time, Enter
    if(historyOpen.has(row.dataset.history))row.after(historyDetailRow(historyOpen.get(row.dataset.history)));
    if(row.dataset.history===focused)open.focus();  // a live refresh must not drop keyboard focus
  });
}
async function toggleHistory(row) {
  const id=row.dataset.history, next=row.nextElementSibling, open=$('.history-open',row);
  if(next?.classList.contains('history-detail')){next.remove();historyOpen.delete(id);open.setAttribute('aria-expanded','false');return;}
  const record=await api(`/api/history/${encodeURIComponent(id)}`);
  historyOpen.set(id,record); row.after(historyDetailRow(record)); open.setAttribute('aria-expanded','true');
}
function historyDetailRow(x) {
  const tr=document.createElement('tr'); tr.className='history-detail';
  tr.innerHTML=`<td colspan="7"><div class="history-panes"><div><strong>${t('Recorded request')}</strong><pre class="history-request"></pre></div><div><strong>${t('Response')}</strong><pre class="history-response"></pre></div></div><div class="controls"><button type="button" class="copy-request">${t('Copy request')}</button><button type="button" class="replay-request">${t('Replay in Playground')}</button></div><div class="history-replay-error error-box" role="alert" hidden></div></td>`;
  $('.history-request',tr).textContent=JSON.stringify(x.request,null,2);
  $('.history-response',tr).textContent=JSON.stringify(x.response,null,2);
  $('.copy-request',tr).onclick=()=>copyText(JSON.stringify(x.request,null,2),t('Request copied'));
  const replay=$('.replay-request',tr), error=$('.history-replay-error',tr);
  if(x.replay_error){replay.disabled=true;error.textContent=x.replay_error;error.hidden=false;}
  replay.onclick=()=>replayRequest(x);
  return tr;
}
function replayRequest(x) {
  if(x.replay_error||!x.replay_request){toast(x.replay_error||t('This request cannot be replayed.'),'error');return;}
  if(!readyModels().some(m=>m.id===x.model)){toast(t('Model {model} is not loaded',{model:x.model}),'error');return;}
  selectedModel=x.model; localStorage.setItem('basal-ui.model',selectedModel);
  $('#playgroundModel').value=selectedModel; setPlaygroundMedia(readyModels().find(m=>m.id===selectedModel)?.media||[]);
  $('#results').innerHTML='';$('#rawDetails').hidden=true;$('#decideError').hidden=true;$('#playgroundForm').classList.remove('comparing');
  setRequest(x.replay_request);
  showView('playground',true);
}
function resetHistory(){historyAfter=null;historyBefore=null;historyBrowsingOlder=false;loadHistory('reset');}
$('#historySearch').oninput=()=>{clearTimeout(historyDebounce);historyDebounce=setTimeout(resetHistory,300);};
$('#historyModel').onchange=resetHistory;
const syncHistorySource=()=>$('#historySource').querySelectorAll('[data-source]').forEach(b=>{b.classList.toggle('active',b.dataset.source===historySource);b.setAttribute('aria-pressed',String(b.dataset.source===historySource));});
syncHistorySource();
$('#historySource').onclick=e=>{const b=e.target.closest('[data-source]');if(!b)return;historySource=b.dataset.source;syncHistorySource();resetHistory();};
$('#historyMore').onclick=()=>loadHistory('more');
$('#clearHistory').onclick=()=>act(async()=>{
  if(!await confirmDialog({title:t('Clear history?'),description:t('Every recorded decision (playground, API and MCP) is deleted.'),confirmLabel:t('Clear history'),destructive:true}))return;
  const d=await api('/api/history',{method:'DELETE'}); toast(tp(d.cleared,'{n} decisions deleted','{n} decisions deleted')); resetHistory();
});
