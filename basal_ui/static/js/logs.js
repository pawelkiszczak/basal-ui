// Logs view.
'use strict';
async function loadLogs() {
  clearTimeout(logTimer);
  if($('#view-logs').hidden)return;
  const out=$('#logOutput'), source=logSource;
  try{
    const d=await api(`/api/logs?source=${encodeURIComponent(source)}&after=${logAfter}${logGeneration?`&generation=${encodeURIComponent(logGeneration)}`:''}`);
    if(source!==logSource)return;  // the user switched tabs meanwhile: that tab's own fetch renders
    if(logGeneration&&d.generation!==logGeneration){out.textContent='';logAfter=0;}
    logGeneration=d.generation;
    $('#logSources').innerHTML=d.sources.map(x=>`<button type="button" data-logsource="${esc(x.id)}" class="${x.id===logSource?'active':''}" aria-pressed="${x.id===logSource}" title="${esc(x.id)}">${esc(x.id==='basal-ui'?'basal-ui':shortName(x.id))}${x.state==='gone'?` <span class="chip">${t('gone')}</span>`:x.id.startsWith('job:')&&x.state==='running'?` <span class="chip">${t('running')}</span>`:''}</button>`).join('');
    const atBottom=out.scrollTop+out.clientHeight>=out.scrollHeight-8;
    if(d.lines.length){
      out.append(document.createTextNode(d.lines.map(x=>source==='basal-ui'?`${clock(x.ts)} ${x.text}`:x.text).join('\n')+'\n'));
      logAfter=d.lines.at(-1).seq;
    }
    $('#logEmpty').hidden=Boolean(out.textContent);
    if($('#logFollow').checked&&atBottom)out.scrollTop=out.scrollHeight;
  }catch(e){
    if(source!==logSource)return;
    if(source!=='basal-ui'){toast(e.message,'error');showView('logs/basal-ui',true);return;}
  }
  if(!$('#view-logs').hidden&&source===logSource)logTimer=setTimeout(loadLogs,2000);
}
$('#logSources').onclick=e=>{const b=e.target.closest('[data-logsource]');if(b)showView(`logs/${encodeURIComponent(b.dataset.logsource)}`,true);};
$('#copyLogs').onclick=()=>copyText($('#logOutput').textContent,t('Log copied'));
$('#clearLogs').onclick=()=>{$('#logOutput').textContent='';$('#logEmpty').hidden=false;};
$('#downloadLogs').onclick=()=>{
  const a=document.createElement('a'), url=URL.createObjectURL(new Blob([$('#logOutput').textContent],{type:'text/plain'}));
  a.href=url; a.download=`${shortName(logSource)}-${new Date().toISOString().slice(0,10)}.log`; a.click(); URL.revokeObjectURL(url);
};
