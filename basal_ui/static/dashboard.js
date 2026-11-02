// The Studio dashboard renders server-owned counters. app.js owns fetching and its single poll cycle.
(() => {
  const root = document.getElementById('dashboard');
  const integer = new Intl.NumberFormat(LANG, {maximumFractionDigits:0});
  const decimal = new Intl.NumberFormat(LANG, {maximumSignificantDigits:3});
  const clock = new Intl.DateTimeFormat(LANG, {hour:'2-digit',minute:'2-digit'});
  const preciseClock = new Intl.DateTimeFormat(LANG, {hour:'2-digit',minute:'2-digit',second:'2-digit'});
  let state = {traffic:null,stats:null,status:null,error:null}, minutes = 15, metric = 'input_tokens_per_s';
  const q = selector => root.querySelector(selector);
  const number = (value, fractional=false) => Number.isFinite(value) ? (fractional ? decimal : integer).format(value) : '—';
  const setText = (selector, text) => {const node=q(selector);if(node.textContent!==text)node.textContent=text;};
  const text = (node,value) => {if(node.textContent!==value)node.textContent=value;};
  const html = (node,value) => {if(node.innerHTML!==value)node.innerHTML=value;};

  root.innerHTML = `<div class="traffic-toolbar">
    <div><strong>${t('Recorded Decision traffic')}</strong><p>${t('Recent traffic from this app process')}</p></div>
    <label class="traffic-window">${t('Traffic window')}<select class="select" id="trafficWindow"><option value="15">${t('Last 15 minutes')}</option><option value="60">${t('Last hour')}</option></select></label>
  </div>
  <p id="trafficNotice" class="traffic-notice" role="status"></p>
  <div class="traffic-kpis">
    ${[['tokens','Input tokens /s','tok/s'],['requests','Requests /s','req/s'],['latency','Mean latency','ms'],['errors','Error rate','%']].map(([id,label,unit])=>`<section class="card traffic-kpi"><h2>${t(label)}</h2><div class="traffic-value"><strong id="traffic-${id}">—</strong><span>${unit}</span></div><p id="traffic-${id}-note"></p></section>`).join('')}
  </div>
  <div class="traffic-panels">
    <section class="card traffic-throughput"><div class="traffic-heading"><div><h2>${t('Traffic over time')}</h2><p>${t('One-minute buckets')}</p></div><div class="traffic-chart-controls" role="group" aria-label="${t('Traffic chart metric')}"><button type="button" data-traffic-metric="input_tokens_per_s" aria-pressed="true">tok/s</button><button type="button" data-traffic-metric="requests_per_s" aria-pressed="false">req/s</button></div></div><div id="trafficChart"></div></section>
    <section class="card traffic-activity"><h2>${t('Model activity')}</h2><div id="trafficActivityCounts" class="traffic-activity-counts"></div><div id="trafficModels" class="traffic-models" tabindex="0" role="region" aria-label="${t('Loaded models')}"></div><a href="#manager/library" class="dashboard-link">${t('Open Model manager')} →</a></section>
  </div>
  <section class="card traffic-endpoints"><div class="traffic-heading"><div><h2>${t('Endpoint traffic')}</h2><p>${t('Recorded Decisions by public entry point')}</p></div><span class="traffic-scope">${t('This app process')}</span></div><div class="traffic-table-wrap"><table><thead><tr><th>${t('Endpoint')}</th><th>${t('Requests')}</th><th>req/s</th><th title="${t('Percentile of bounded latency samples, not every Decision')}">${t('p95 (sampled)')} · ms</th><th>${t('Errors')}</th></tr></thead><tbody id="trafficEndpoints"></tbody></table></div></section>
  <p class="traffic-definition">${t('Input tokens processed per second, not generated output tokens.')} ${t('Lifetime model usage is shared with Model manager; recent traffic is local to this app process.')}</p>`;

  function renderChart(traffic) {
    const container=q('#trafficChart');
    if(!container.querySelector('svg'))container.innerHTML=`<p class="traffic-empty"></p><svg class="traffic-chart" viewBox="0 0 680 204" role="img" hidden>
      <defs><linearGradient id="trafficFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="var(--accent)" stop-opacity=".18"/><stop offset="100%" stop-color="var(--accent)" stop-opacity="0"/></linearGradient></defs>
      ${[0,.5,1].map(fraction=>`<line x1="48" x2="652" y1="${164-fraction*138}" y2="${164-fraction*138}" stroke="var(--line)" stroke-dasharray="3 5"/><text class="traffic-axis" x="0" y="${168-fraction*138}" fill="var(--muted)"></text>`).join('')}
      <path class="traffic-area" fill="url(#trafficFill)"/><path class="traffic-line" fill="none" stroke="var(--accent)" stroke-width="2.5" stroke-linejoin="round"/>
      <circle r="3.5" fill="var(--accent)"/><text class="traffic-start" x="48" y="194" fill="var(--muted)"></text><text class="traffic-end" x="652" y="194" text-anchor="end" fill="var(--muted)"></text></svg>`;
    const series=traffic?.series||[], available=traffic&&!state.error&&traffic.requests&&series.length;
    const svg=container.querySelector('svg'), empty=container.querySelector('.traffic-empty');
    svg.toggleAttribute('hidden',!available); empty.hidden=Boolean(available);
    if(!available){
      text(empty,t(state.error?'Traffic data unavailable':traffic?'No Decisions recorded in this window.':'Waiting for traffic data…'));
      return;
    }
    const values=series.map(bucket=>Number(bucket[metric])||0), maximum=(Math.max(...values)||1)*1.15;
    const coordinates=values.map((value,index)=>({x:48+index*604/Math.max(1,values.length-1),y:164-value/maximum*138}));
    const path=coordinates.map((point,index)=>`${index?'L':'M'}${point.x},${point.y}`).join(' '), last=coordinates.at(-1);
    svg.setAttribute('aria-label',t(metric==='input_tokens_per_s'?'Input throughput over time':'Requests over time'));
    svg.querySelectorAll('.traffic-axis').forEach((node,index)=>text(node,number(maximum*index/2,true)));
    svg.querySelector('.traffic-area').setAttribute('d',coordinates.length>1?`${path} L${last.x},164 L48,164 Z`:'');
    svg.querySelector('.traffic-line').setAttribute('d',coordinates.length>1?path:'');
    svg.querySelector('circle').setAttribute('cx',last.x); svg.querySelector('circle').setAttribute('cy',last.y);
    const formatter=series.length<3?preciseClock:clock;
    text(svg.querySelector('.traffic-start'),formatter.format(new Date(series[0].start*1000)));
    text(svg.querySelector('.traffic-end'),formatter.format(new Date(traffic.window_end*1000)));
  }

  function renderModels() {
    const models = state.status?.models;
    if (!models) {
      q('#trafficActivityCounts').textContent = '';
      html(q('#trafficModels'),`<p class="muted">${t('Model status unavailable')}</p>`);
      return;
    }
    const ready = models.filter(model=>model.state==='ready'&&model.health?.reachable!==false);
    const inFlight = models.reduce((sum,model)=>sum+(model.inflight?.length||0),0);
    const queued = ready.every(model=>Number.isFinite(model.health?.queued)) ? ready.reduce((sum,model)=>sum+model.health.queued,0) : null;
    html(q('#trafficActivityCounts'),`<span>${t('Ready models')}: <b>${ready.length}</b></span><span>${t('In flight')}: <b>${inFlight}</b></span><span>${t('Queued')}: <b>${number(queued)}</b></span>`);
    const container=q('#trafficModels'), existing=new Map([...container.querySelectorAll('.traffic-model')].map(node=>[node.dataset.model,node]));
    if(!models.length){html(container,`<p class="muted">${t('No model loaded')}</p>`);return;}
    container.querySelectorAll(':scope > p').forEach(node=>node.remove());
    models.forEach((model,index)=>{
      let node=existing.get(model.id); existing.delete(model.id);
      if(!node){
        node=document.createElement('article');node.className='traffic-model';node.dataset.model=model.id;
        node.innerHTML=`<div><span class="status-dot"></span><strong></strong><span class="chip"></span></div><p>${t('Lifetime requests')}: <span class="traffic-model-requests"></span> · ${t('Input tokens')}: <span class="traffic-model-tokens"></span></p>`;
        node.querySelector('strong').title=model.id;
      }
      const usage=state.stats?.models?.[model.id], reachable=model.health?.reachable!==false;
      node.querySelector('.status-dot').className=`status-dot${model.state==='ready'&&reachable?' ready':''}`;
      text(node.querySelector('strong'),shortName(model.id));text(node.querySelector('.chip'),t(reachable?model.state:'unreachable'));
      text(node.querySelector('.traffic-model-requests'),number(state.stats?(usage?.requests??0):null));
      text(node.querySelector('.traffic-model-tokens'),number(state.stats?(usage?.input_tokens??0):null));
      if(container.children[index]!==node)container.insertBefore(node,container.children[index]||null);
    });
    existing.forEach(node=>node.remove());
  }

  function render() {
    const traffic = state.traffic?.window_seconds===minutes*60 ? state.traffic : null;
    const available = traffic && !state.error;
    const count = available ? traffic.requests : null;
    setText('#traffic-tokens',number(available?traffic.input_tokens_per_s:null,true));
    setText('#traffic-requests',number(available?traffic.requests_per_s:null,true));
    setText('#traffic-latency',number(available?traffic.avg_latency_ms:null));
    setText('#traffic-errors',number(count?traffic.errors/count*100:null,true));
    setText('#traffic-tokens-note',t('Observed-window average'));
    setText('#traffic-requests-note',`${t('Decisions in this window')}: ${number(count)}`);
    setText('#traffic-latency-note',t('Request-weighted mean'));
    setText('#traffic-errors-note',`${t('Failed Decisions')}: ${number(available?traffic.errors:null)}`);
    setText('#trafficNotice',state.error?t('Traffic unavailable: {message}',{message:state.error}):available?t('Observed since {time}',{time:preciseClock.format(new Date(traffic.observed_since*1000))}):t('Waiting for traffic data…'));
    q('#trafficNotice').classList.toggle('traffic-error',Boolean(state.error));
    renderChart(traffic);
    renderModels();
    const endpoints = available ? traffic.endpoints || [] : [];
    const container=q('#trafficEndpoints'), existing=new Map([...container.querySelectorAll('[data-endpoint]')].map(node=>[node.dataset.endpoint,node]));
    if(!endpoints.length){html(container,`<tr><td colspan="5" class="traffic-no-endpoints">${t(state.error?'Traffic data unavailable':available?'No Decisions recorded in this window.':'Waiting for traffic data…')}</td></tr>`);return;}
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
    minutes=Number(event.target.value);
    render();
    document.dispatchEvent(new CustomEvent('dashboard:window',{detail:{minutes}}));
  };
  root.querySelectorAll('[data-traffic-metric]').forEach(button=>button.onclick=()=>{
    metric=button.dataset.trafficMetric;
    root.querySelectorAll('[data-traffic-metric]').forEach(control=>control.setAttribute('aria-pressed',String(control===button)));
    renderChart(state.traffic?.window_seconds===minutes*60?state.traffic:null);
  });
  window.updateDashboard = data => {state=data;render();};
})();
