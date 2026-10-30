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
    if (!traffic || state.error) {
      q('#trafficChart').innerHTML = `<p class="traffic-empty">${t(state.error?'Traffic data unavailable':'Waiting for traffic data…')}</p>`;
      return;
    }
    const series = traffic.series || [];
    if (!traffic.requests || !series.length) {
      q('#trafficChart').innerHTML = `<p class="traffic-empty">${t('No Decisions recorded in this window.')}</p>`;
      return;
    }
    const values = series.map(bucket=>Number(bucket[metric])||0);
    const maximum = (Math.max(...values)||1)*1.15;
    const coordinates = values.map((value,index)=>({x:48+index*604/Math.max(1,values.length-1),y:164-value/maximum*138}));
    const path = coordinates.map((point,index)=>`${index?'L':'M'}${point.x},${point.y}`).join(' ');
    const labels = [series[0].start, traffic.window_end];
    const formatter = series.length < 3 ? preciseClock : clock;
    const last = coordinates.at(-1);
    q('#trafficChart').innerHTML = `<svg class="traffic-chart" viewBox="0 0 680 204" role="img" aria-label="${t(metric==='input_tokens_per_s'?'Input throughput over time':'Requests over time')}">
      <defs><linearGradient id="trafficFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="var(--accent)" stop-opacity=".18"/><stop offset="100%" stop-color="var(--accent)" stop-opacity="0"/></linearGradient></defs>
      ${[0,.5,1].map(fraction=>`<line x1="48" x2="652" y1="${164-fraction*138}" y2="${164-fraction*138}" stroke="var(--line)" stroke-dasharray="3 5"/><text x="0" y="${168-fraction*138}" fill="var(--muted)">${number(maximum*fraction,true)}</text>`).join('')}
      ${coordinates.length>1?`<path d="${path} L${last.x},164 L48,164 Z" fill="url(#trafficFill)"/><path d="${path}" fill="none" stroke="var(--accent)" stroke-width="2.5" stroke-linejoin="round"/>`:''}
      <circle cx="${last.x}" cy="${last.y}" r="3.5" fill="var(--accent)"/>
      ${labels.map((stamp,index)=>`<text x="${index?652:48}" y="194" text-anchor="${index?'end':'start'}" fill="var(--muted)">${formatter.format(new Date(stamp*1000))}</text>`).join('')}
    </svg>`;
  }

  function renderModels() {
    const models = state.status?.models;
    if (!models) {
      q('#trafficActivityCounts').textContent = '';
      q('#trafficModels').innerHTML = `<p class="muted">${t('Model status unavailable')}</p>`;
      return;
    }
    const ready = models.filter(model=>model.state==='ready'&&model.health?.reachable!==false);
    const inFlight = models.reduce((sum,model)=>sum+(model.inflight?.length||0),0);
    const queued = ready.every(model=>Number.isFinite(model.health?.queued)) ? ready.reduce((sum,model)=>sum+model.health.queued,0) : null;
    q('#trafficActivityCounts').innerHTML = `<span>${t('Ready models')}: <b>${ready.length}</b></span><span>${t('In flight')}: <b>${inFlight}</b></span><span>${t('Queued')}: <b>${number(queued)}</b></span>`;
    q('#trafficModels').innerHTML = models.length ? models.map(model=>{
      const usage = state.stats?.models?.[model.id];
      const reachable = model.health?.reachable!==false;
      return `<article class="traffic-model"><div><span class="status-dot${model.state==='ready'&&reachable?' ready':''}"></span><strong title="${esc(model.id)}">${esc(shortName(model.id))}</strong><span class="chip">${esc(t(reachable?model.state:'unreachable'))}</span></div><p>${t('Lifetime requests')}: <span class="traffic-model-requests">${number(state.stats?(usage?.requests??0):null)}</span> · ${t('Input tokens')}: <span class="traffic-model-tokens">${number(state.stats?(usage?.input_tokens??0):null)}</span></p></article>`;
    }).join('') : `<p class="muted">${t('No model loaded')}</p>`;
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
    q('#trafficEndpoints').innerHTML = endpoints.length ? endpoints.map(row=>`<tr><td><span class="traffic-method">${esc(row.method)}</span><code>${esc(row.endpoint)}</code></td><td>${number(row.requests)}</td><td>${number(row.requests_per_s,true)}</td><td>${number(row.p95_ms)}</td><td>${number(row.errors)}</td></tr>`).join('') : `<tr><td colspan="5" class="traffic-no-endpoints">${t(state.error?'Traffic data unavailable':available?'No Decisions recorded in this window.':'Waiting for traffic data…')}</td></tr>`;
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
