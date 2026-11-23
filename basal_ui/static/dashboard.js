// The Studio dashboard renders server-owned counters. app.js owns fetching and its single poll cycle.
(() => {
  const root = document.getElementById('dashboard');
  const integer = new Intl.NumberFormat(LANG, {maximumFractionDigits:0});
  const decimal = new Intl.NumberFormat(LANG, {maximumSignificantDigits:3});
  const clock = new Intl.DateTimeFormat(LANG, {hour:'2-digit',minute:'2-digit'});
  const preciseClock = new Intl.DateTimeFormat(LANG, {hour:'2-digit',minute:'2-digit',second:'2-digit'});
  let state = {traffic:null,history:null,memoryHistory:null,stats:null,status:null,error:null,recentError:null,memory:null,memoryError:null,memoryHistoryError:null,range:'24h',refreshSeconds:2}, metric = 'input_tokens_per_s';
  const q = selector => root.querySelector(selector);
  const number = (value, fractional=false) => Number.isFinite(value) ? (fractional ? decimal : integer).format(value) : '—';
  const setText = (selector, text) => {const node=q(selector);if(node.textContent!==text)node.textContent=text;};
  const text = (node,value) => {if(node.textContent!==value)node.textContent=value;};
  const html = (node,value) => {if(node.innerHTML!==value)node.innerHTML=value;};

  root.innerHTML = `<section class="card memory-panel" aria-labelledby="memoryTitle"><div class="traffic-heading"><div><h2 id="memoryTitle">${t('Live memory')}</h2><p>${t('Host and loaded runtime measurements')}</p></div><span id="memoryTime" class="traffic-scope"></span></div><div id="memoryContent"></div></section>
  <section class="card memory-history-panel"><div class="traffic-heading"><div><h2>${t('Memory over time')}</h2><p>${t('Peak per display bucket')}</p></div><span id="memoryCollection" class="traffic-scope"></span></div><p id="memoryHistoryNotice" class="traffic-notice" role="status"></p><div id="memoryHistoryChart"></div></section>
  <div class="traffic-toolbar">
    <div><strong>${t('Recorded Decision traffic')}</strong><p>${t('Recorded traffic collected from this app')}</p></div>
    <div class="traffic-controls">
      <label class="traffic-window">${t('Time range')}<select class="select" id="trafficWindow">${[['1h','Last hour'],['24h','Last 24 hours'],['7d','Last 7 days'],['30d','Last 30 days'],['all','All time']].map(([value,label])=>`<option value="${value}">${t(label)}</option>`).join('')}</select></label>
      <label class="traffic-window">${t('Display refresh')}<select class="select" id="dashboardRefresh">${[['1','Every 1 second'],['2','Every 2 seconds'],['5','Every 5 seconds'],['15','Every 15 seconds'],['30','Every 30 seconds']].map(([value,label])=>`<option value="${value}">${t(label)}</option>`).join('')}</select></label>
    </div>
  </div>
  <p id="trafficNotice" class="traffic-notice" role="status"></p>
  <p id="trafficCollection" class="traffic-notice"></p>
  <div class="traffic-kpis">
    ${[['tokens','Input tokens /s','tok/s'],['requests','Requests /s','req/s'],['latency','Mean latency','ms'],['errors','Error rate','%']].map(([id,label,unit])=>`<section class="card traffic-kpi"><h2>${t(label)}</h2><div class="traffic-value"><strong id="traffic-${id}">—</strong><span>${unit}</span></div><p id="traffic-${id}-note"></p></section>`).join('')}
  </div>
  <div class="traffic-panels">
    <section class="card traffic-throughput"><div class="traffic-heading"><div><h2>${t('Traffic over time')}</h2><p>${t('Peak per display bucket')}</p></div><div class="traffic-chart-controls" role="group" aria-label="${t('Traffic chart metric')}"><button type="button" data-traffic-metric="input_tokens_per_s" aria-pressed="true">tok/s</button><button type="button" data-traffic-metric="requests_per_s" aria-pressed="false">req/s</button></div></div><div id="trafficChart"></div></section>
    <section class="card traffic-activity"><h2>${t('Model activity')}</h2><div id="trafficActivityCounts" class="traffic-activity-counts"></div><div id="trafficModels" class="traffic-models" tabindex="0" role="region" aria-label="${t('Loaded models')}"></div><a href="#manager/library" class="dashboard-link">${t('Open Model manager')} →</a></section>
  </div>
  <section class="card traffic-endpoints"><div class="traffic-heading"><div><h2>${t('Endpoint traffic')}</h2><p>${t('Recent recorded Decisions by public entry point')}</p></div><span class="traffic-scope">${t('Last 15 minutes')}</span></div><div class="traffic-table-wrap"><table><thead><tr><th>${t('Endpoint')}</th><th>${t('Requests')}</th><th>req/s</th><th title="${t('Percentile of bounded latency samples, not every Decision')}">${t('p95 (sampled)')} · ms</th><th>${t('Errors')}</th></tr></thead><tbody id="trafficEndpoints"></tbody></table></div></section>
  <p class="traffic-definition">${t('Input tokens processed per second, not generated output tokens.')} ${t('Lifetime model usage is shared with Model manager; dated traffic history is shared across apps using the same settings file.')}</p>`;

  const bytes = value => value == null ? t('Not measured') : `${(value / 1024**3).toFixed(2)} GiB`;
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const memoryNote = (note, params) => {
    const usage = note.match(/^vLLM KV-cache usage by engine: (.+)\. Process RSS includes vLLM runtime overhead\.$/);
    return usage
      ? t('vLLM KV-cache usage by engine: {usage}. Process RSS includes vLLM runtime overhead.', {usage: usage[1]})
      : t(note, params);
  };
  function renderMemory() {
    const memory=state.memory, host=memory?.host, target=q('#memoryContent');
    setText('#memoryTime',memory?.time?preciseClock.format(new Date(memory.time*1000)):'');
    if(!memory||state.memoryError){html(target,`<p class="memory-unavailable">${t('Memory data unavailable')}</p>`);return;}
    const runtimes=memory.runtimes||[];
    const measured=runtimes.filter(runtime=>runtime.process_bytes!=null);
    const hasBreakdown=memory.other!=null;
    const used=host?.ram_used, total=host?.ram_total;
    const barDescription=hasBreakdown
      ? [...measured.map(runtime=>`${runtime.label}: ${bytes(runtime.process_bytes)}`),`${t('System and other apps')}: ${bytes(memory.other)}`].join('; ')
      : t('System and other apps: breakdown unavailable');
    const segments=hasBreakdown?measured.map((runtime,index)=>`<span class="memory-segment memory-segment-${index%4}" style="width:${Math.min(100,runtime.process_bytes/total*100)}%" title="${escape(runtime.label)} · ${bytes(runtime.process_bytes)}"></span>`).join('')+`<span class="memory-other" style="width:${Math.min(100,memory.other/total*100)}%" title="${t('System and other apps')} · ${bytes(memory.other)}"></span>`:'';
    const hostFill=used==null||!total?'0':Math.min(100,used/total*100);
    const barContent=hasBreakdown?segments:`<span class="memory-host-used" style="width:${hostFill}%"></span>`;
    const runtimeRows=runtimes.map(runtime=>{
      const models=runtime.models||[];
      return `<section class="memory-runtime"><div class="memory-runtime-head"><strong>${escape(runtime.label)}</strong><span>${bytes(runtime.process_bytes)}</span></div>${models.length?models.map(model=>{const label=model.aggregate!=null?tp(model.aggregate,'{n} loaded model','{n} loaded models'):model.id;const size=(model.size_estimated?`${bytes(model.bytes)} (${t('Estimated')})`:bytes(model.bytes))+` · ${t('Pool: {pool}').replace('{pool}',t(model.pool==='gpu'?'VRAM':model.pool==='ram'?'RAM':model.pool==='unified'?'Unified':'Mixed or unknown'))}`;return `<article class="memory-model"><div class="memory-model-head"><strong>${escape(label)}</strong><span>${size}</span></div><dl><div><dt>${t('Context bytes')}</dt><dd>${bytes(model.context_bytes)}</dd></div><div><dt>${t('Hot cache')} (${t('Hot cache pool: {pool}').replace('{pool}',model.cache_pool||'RAM')})</dt><dd>${bytes(model.cache_bytes)}</dd></div><div><dt>${t('Disk-backed cold cache')}</dt><dd>${bytes(model.cold_cache_bytes)}</dd></div></dl></article>`;}).join(''):`<p class="memory-unavailable">${runtime.status==='empty'||runtime.status==='ok'?t('No loaded models'):t('Runtime memory unavailable')}</p>`}${runtime.note?`<p class="memory-note">${escape(memoryNote(runtime.note,{count:models.find(model=>model.aggregate!=null)?.aggregate||0}))}</p>`:''}</section>`;
    }).join('');
    const gpu=(host?.gpu||[]).map(device=>`<div class="memory-device"><strong>${escape(device.name)}</strong><span>${bytes(device.used)} / ${bytes(device.total)}</span></div>`).join('');
    html(target,`<div class="memory-host"><div class="memory-runtime-head"><strong>${t('Host RAM')}</strong><span>${bytes(used)} / ${bytes(total)}</span></div><div class="memory-track" role="img" aria-label="${escape(`${t('Host RAM')}: ${bytes(used)} / ${bytes(total)}; ${barDescription}`)}">${barContent}</div><p>${memory.other==null?t('System and other apps: breakdown unavailable'):`${t('System and other apps')}: ${bytes(memory.other)}`}</p></div><section class="memory-gpu"><h3>${t('Discrete GPU memory')}</h3>${gpu||`<p class="memory-unavailable">${t('Not measured')}</p>`}</section>${host?.unified?`<p class="memory-note">${t('Unified memory is shared; allocations are counted once.')}</p>`:''}<div class="memory-runtimes">${runtimeRows}</div>`);
  }

  function renderMemoryHistory() {
    const history=state.memoryHistory?.range===state.range?state.memoryHistory:null, container=q('#memoryHistoryChart');
    const started=history?.collection_started;
    setText('#memoryCollection',started?t('Collection started {time}',{time:new Intl.DateTimeFormat(LANG,{dateStyle:'medium',timeStyle:'short'}).format(new Date(started*1000))}):t('Collection has not started.'));
    setText('#memoryHistoryNotice',state.memoryHistoryError?t('Memory history unavailable: {message}',{message:state.memoryHistoryError}):'');
    const start=history?.window_start,end=history?.window_end,span=end-start,rows=history?.series||[];
    const names=['host','other',...new Set(rows.flatMap(row=>Object.keys(row.runtimes||{})))].filter(name=>
      rows.some(row=>name==='host'?row.host:name==='other'?row.other:row.runtimes?.[name])
    );
    if(!history||!names.length||!(span>0)){html(container,`<p class="traffic-empty">${t(state.memoryHistoryError?'Memory history unavailable':history?'No memory measurements in this time range.':'Waiting for memory samples…')}</p>`);return;}
    const values=row=>names.map(name=>name==='host'?row.host:name==='other'?row.other:row.runtimes?.[name]).filter(Boolean);
    const maxBytes=Math.max(1024**3,...rows.flatMap(row=>values(row).map(value=>value.max_bytes||0)))*1.15;
    const paths=names.map((name,index)=>{
      let d='',markers='',last=false,previousEnd=null;
      for(const row of rows){
        const value=name==='host'?row.host:name==='other'?row.other:row.runtimes?.[name];
        if(!value){last=false;continue;}
        if(previousEnd!=null&&row.start>previousEnd)last=false;
        const x=48+Math.max(0,Math.min(1,(row.start+row.bucket_seconds/2-start)/span))*604;
        const y=164-value.max_bytes/maxBytes*138;
        d+=`${last?'L':'M'}${x},${y} `;
        markers+=`<circle cx="${x}" cy="${y}" r="2.5" fill="currentColor"/>`;
        last=true;previousEnd=row.start+row.bucket_seconds;
      }
      return `<g class="memory-history-line memory-history-line-${index%4}"><path d="${d}"/>${markers}</g>`;
    }).join('');
    const formatter=span<86400?preciseClock:new Intl.DateTimeFormat(LANG,{month:'short',day:'numeric'});
    const label=name=>name==='host'?t('Host RAM'):name==='other'?t('System and other apps'):name;
    html(container,`<svg class="traffic-chart memory-history-chart" viewBox="0 0 680 204" role="img" aria-label="${t('Memory over time')}"><line x1="48" x2="652" y1="164" y2="164" stroke="var(--line)"/><text class="traffic-axis" x="0" y="168" fill="var(--muted)">0</text><text class="traffic-axis" x="0" y="30" fill="var(--muted)">${bytes(maxBytes/1.15)}</text>${paths}<text class="traffic-start" x="48" y="194" fill="var(--muted)">${formatter.format(new Date(start*1000))}</text><text class="traffic-end" x="652" y="194" text-anchor="end" fill="var(--muted)">${formatter.format(new Date(end*1000))}</text></svg><div class="memory-history-legend">${names.map((name,index)=>`<span class="memory-history-legend-${index%4}">${escape(label(name))}</span>`).join('')}</div>`);
  }
  function renderChart(history) {
    const container=q('#trafficChart');
    if(!container.querySelector('svg'))container.innerHTML=`<p class="traffic-empty"></p><svg class="traffic-chart" viewBox="0 0 680 204" role="img" hidden>
      <defs><linearGradient id="trafficFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="var(--accent)" stop-opacity=".18"/><stop offset="100%" stop-color="var(--accent)" stop-opacity="0"/></linearGradient></defs>
      ${[0,.5,1].map(fraction=>`<line x1="48" x2="652" y1="${164-fraction*138}" y2="${164-fraction*138}" stroke="var(--line)" stroke-dasharray="3 5"/><text class="traffic-axis" x="0" y="${168-fraction*138}" fill="var(--muted)"></text>`).join('')}
      <path class="traffic-area" fill="url(#trafficFill)"/><path class="traffic-line" fill="none" stroke="var(--accent)" stroke-width="2.5" stroke-linejoin="round"/><path class="traffic-points" fill="var(--accent)"/>
      <text class="traffic-start" x="48" y="194" fill="var(--muted)"></text><text class="traffic-end" x="652" y="194" text-anchor="end" fill="var(--muted)"></text></svg>`;
    const svg=container.querySelector('svg'), empty=container.querySelector('.traffic-empty');
    const start=history?.window_start, end=history?.window_end, span=end-start;
    const samples=history?.series||[], available=!state.error&&samples.length&&span>0;
    svg.toggleAttribute('hidden',!available); empty.hidden=Boolean(available);
    if(!available){
      text(empty,t(state.error?'Traffic data unavailable':history?'No Decisions recorded in this time range.':'Waiting for traffic data…'));
      return;
    }
    const count=120, points=Array(count).fill(null), step=span/count;
    for(const sample of samples){
      const value=(sample[metric==='input_tokens_per_s'?'input_tokens':'requests'])/sample.bucket_seconds;
      const first=Math.max(0,Math.floor((sample.start-start)/step));
      const last=Math.min(count-1,Math.ceil((sample.start+sample.bucket_seconds-start)/step)-1);
      for(let index=first;index<=last;index++)points[index]=Math.max(points[index]??0,value);
    }
    let maximum=1;
    for(const value of points)if(value!==null)maximum=Math.max(maximum,value);
    maximum*=1.15;
    const coordinates=points.map((value,index)=>value===null?null:{x:48+index*604/(count-1),y:164-value/maximum*138});
    let path='',area='',markers='',last=null;
    coordinates.forEach((point,index)=>{
      if(!point){last=null;return;}
      path+=`${last?'L':'M'}${point.x},${point.y} `;
      area+=`${last?'L':'M'}${point.x},${point.y} `;
      markers+=`M${point.x-2.5},${point.y}a2.5,2.5 0 1,0 5,0a2.5,2.5 0 1,0 -5,0 `;
      if(!coordinates[index+1])area+=`L${point.x},164 Z `;
      last=point;
    });
    svg.setAttribute('aria-label',t(metric==='input_tokens_per_s'?'Input throughput over time':'Requests over time'));
    svg.querySelectorAll('.traffic-axis').forEach((node,index)=>text(node,number(maximum*index/2,true)));
    svg.querySelector('.traffic-area').setAttribute('d',area);
    svg.querySelector('.traffic-line').setAttribute('d',path);
    svg.querySelector('.traffic-points').setAttribute('d',markers);
    const formatter=span<86400?preciseClock:new Intl.DateTimeFormat(LANG,{month:'short',day:'numeric'});
    text(svg.querySelector('.traffic-start'),formatter.format(new Date(start*1000)));
    text(svg.querySelector('.traffic-end'),formatter.format(new Date(end*1000)));
  }
  function renderModels() {
    const models = state.status?.models;
    if (!models) {
      q('#trafficActivityCounts').textContent = '';
      html(q('#trafficModels'),`<p class="muted">${t('Model status unavailable')}</p>`);
      return;
    }
    const ready = models.filter(model=>model.state==='ready'&&model.health?.reachable!==false&&model.health?.status!=='stalled');
    const inFlight = models.reduce((sum,model)=>sum+(model.inflight?.length||0),0);
    const queued = ready.every(model=>Number.isFinite(model.health?.queued)) ? ready.reduce((sum,model)=>sum+model.health.queued,0) : null;
    html(q('#trafficActivityCounts'),`<span>${t('Ready models')}: <b>${ready.length}</b></span><span>${t('In flight')}: <b>${inFlight}</b></span><span>${t('Queued')}: <b>${number(queued)}</b></span>`);
    const container=q('#trafficModels'), existing=new Map([...container.querySelectorAll('.traffic-model')].map(node=>[node.dataset.model,node]));
    const idle = Object.entries(state.status?.idle_unloads||{}).filter(([id])=>!models.some(model=>model.id===id));
    if(!models.length && !idle.length){html(container,`<p class="muted">${t('No model loaded')}</p>`);return;}
    container.querySelectorAll(':scope > p').forEach(node=>node.remove());
    models.forEach((model,index)=>{
      let node=existing.get(model.id); existing.delete(model.id);
      if(node?.dataset.idle){node.remove();node=null;}
      if(!node){
        node=document.createElement('article');node.className='traffic-model';node.dataset.model=model.id;
        node.innerHTML=`<div><span class="status-dot"></span><strong></strong><span class="chip"></span></div><p class="traffic-model-health"></p><p>${t('Lifetime requests')}: <span class="traffic-model-requests"></span> · ${t('Input tokens')}: <span class="traffic-model-tokens"></span></p>`;
        node.querySelector('strong').title=model.id;
      }
      const usage=state.stats?.models?.[model.id], reachable=model.health?.reachable!==false;
      const health=model.health||{}, stalled=health.status==='stalled';
      node.querySelector('.status-dot').className=`status-dot${model.state==='ready'&&reachable&&!stalled?' ready':''}`;
      text(node.querySelector('strong'),shortName(model.id));text(node.querySelector('.chip'),`${model.kind==='comparison'?`${t('Comparison')} · `:''}${t(stalled?'stalled':reachable?model.state:'unreachable')}`);
      const age=stalled&&Number.isFinite(health.engine_call_s)?t('Engine call age: {seconds} s',{seconds:number(health.engine_call_s,true)}):'';
      const lanes=health.long&&Number.isFinite(health.running)&&Number.isFinite(health.queued)?t('Main: {running} running · {queued} queued; long: {longRunning} running · {longQueued} queued',{running:number(health.running-health.long.running),queued:number(health.queued-health.long.queued),longRunning:number(health.long.running),longQueued:number(health.long.queued)}):'';
      text(node.querySelector('.traffic-model-health'),[age,lanes].filter(Boolean).join(' · '));
      text(node.querySelector('.traffic-model-requests'),number(state.stats?(usage?.requests??0):null));
      text(node.querySelector('.traffic-model-tokens'),number(state.stats?(usage?.input_tokens??0):null));
      if(container.children[index]!==node)container.insertBefore(node,container.children[index]||null);
    });
    idle.forEach(([id,reason],index)=>{
      let node=existing.get(id); existing.delete(id);
      if(!node){node=document.createElement('article');node.className='traffic-model';node.dataset.model=id;}
      node.dataset.idle='1';
      text(node,`${id}: ${tp(reason.minutes,'Unloaded after {n} idle minute at {time}','Unloaded after {n} idle minutes at {time}',{time:new Date(reason.at*1000).toLocaleString()})}`);
      if(container.children[models.length+index]!==node)container.insertBefore(node,container.children[models.length+index]||null);
    });
    existing.forEach(node=>node.remove());
  }

  function render() {
    const history=state.history?.range===state.range?state.history:null, recent=state.traffic, available=history&&!state.error;
    q('#trafficWindow').value=state.range;
    q('#dashboardRefresh').value=String(state.refreshSeconds||2);
    const rows=history?.series||[], requests=rows.reduce((sum,row)=>sum+row.requests,0), errors=rows.reduce((sum,row)=>sum+row.errors,0);
    const tokens=rows.reduce((sum,row)=>sum+row.input_tokens,0), latency=rows.reduce((sum,row)=>sum+row.avg_latency_ms*row.requests,0);
    const total=requests, totalErrors=errors, totalTokens=tokens;
    const meanLatency=requests?latency/requests:null;
    const elapsed=history?.window_start==null?0:Math.max(0,history.window_end-history.window_start);
    setText('#traffic-tokens',number(available&&elapsed?totalTokens/elapsed:null,true));
    setText('#traffic-requests',number(available&&elapsed?total/elapsed:null,true));
    setText('#traffic-latency',number(available?meanLatency:null));
    setText('#traffic-errors',number(available&&total?totalErrors/total*100:null,true));
    setText('#traffic-tokens-note',t('Observed-window average'));
    setText('#traffic-requests-note',`${t('Decisions in this time range')}: ${number(available?total:null)}`);
    setText('#traffic-latency-note',t('Request-weighted mean'));
    setText('#traffic-errors-note',`${t('Failed Decisions')}: ${number(available?totalErrors:null)}`);
    setText('#trafficNotice',state.error?t('Traffic unavailable: {message}',{message:state.error}):available?'':t('Waiting for traffic data…'));
    q('#trafficNotice').classList.toggle('traffic-error',Boolean(state.error));
    renderMemory();
    renderMemoryHistory();
    const started=history?.collection_started;
    setText('#trafficCollection',started?t('Collection started {time}',{time:new Intl.DateTimeFormat(LANG,{dateStyle:'medium',timeStyle:'short'}).format(new Date(started*1000))}):t('Collection will start when the first Decision is recorded.'));
    renderChart(history);
    renderModels();
    const endpoints = recent && !state.recentError ? recent.endpoints || [] : [];
    const container=q('#trafficEndpoints'), existing=new Map([...container.querySelectorAll('[data-endpoint]')].map(node=>[node.dataset.endpoint,node]));
    if(!endpoints.length){html(container,`<tr><td colspan="5" class="traffic-no-endpoints">${t(state.recentError?'Traffic data unavailable':recent?'No Decisions recorded in this window.':'Waiting for traffic data…')}</td></tr>`);return;}
    container.querySelectorAll('tr:not([data-endpoint])').forEach(node=>node.remove());
    endpoints.forEach((row,index)=>{
      const key=`${row.method} ${row.endpoint}`;let node=existing.get(key);existing.delete(key);
      if(!node){node=document.createElement('tr');node.dataset.endpoint=key;node.innerHTML='<td><span class="traffic-method"></span><code></code></td><td></td><td></td><td></td><td></td>';}
      text(node.querySelector('.traffic-method'),row.method);text(node.querySelector('code'),row.endpoint);
      [number(row.requests),number(row.requests_per_s,true),number(row.p95_ms),number(row.errors)].forEach((value,column)=>text(node.children[column+1],value));
      if(container.children[index]!==node)container.insertBefore(node,container.children[index]||null);
    });
    existing.forEach(node=>node.remove());
  }

  q('#trafficWindow').onchange = event => {
    state.range=event.target.value;
    render();
    document.dispatchEvent(new CustomEvent('dashboard:range',{detail:{range:state.range}}));
  };
  q('#dashboardRefresh').onchange = event => document.dispatchEvent(new CustomEvent('dashboard:refresh',{detail:{seconds:Number(event.target.value)}}));
  root.querySelectorAll('[data-traffic-metric]').forEach(button=>button.onclick=()=>{
    metric=button.dataset.trafficMetric;
    root.querySelectorAll('[data-traffic-metric]').forEach(control=>control.setAttribute('aria-pressed',String(control===button)));
    renderChart(state.history?.range===state.range?state.history:null);
  });
  window.updateDashboard = data => {state={...state,...data};render();};

})();
