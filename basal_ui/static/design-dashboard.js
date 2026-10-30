/* THROWAWAY dashboard for the three design templates, not a production telemetry implementation.
 * Demo is deterministic illustrative traffic. Live uses existing /api/stats and /api/status only.
 * Input-token throughput is counter delta / elapsed wall time, not generation speed.
 */
(() => {
  if (document.body.dataset.design === 'original') return;
  Object.assign(PL, {
    'Dashboard': 'Pulpit',
    'Throughput, request traffic and serving health.': 'Przepustowość, ruch żądań i stan obsługi.',
    'Demo data': 'Dane demonstracyjne',
    'Live counters': 'Rzeczywiste liczniki',
    'Traffic data source': 'Źródło danych o ruchu',
    'Last 15 minutes': 'Ostatnie 15 minut',
    'Last hour': 'Ostatnia godzina',
    'Traffic window': 'Zakres czasu',
    'Illustrative traffic — not measurements from this machine.': 'Przykładowy ruch — nie są to pomiary z tego komputera.',
    'Rates: last refresh interval. Counters: all recorded Decisions.': 'Tempo: ostatni interwał odświeżania. Liczniki: wszystkie zapisane Decyzje.',
    'Input tokens /s': 'Tokeny wejściowe /s',
    'Requests /s': 'Żądania /s',
    'Mean latency': 'Średnie opóźnienie',
    'Error rate': 'Odsetek błędów',
    'Window average': 'Średnia w wybranym okresie',
    'Since previous refresh': 'Od poprzedniego odświeżenia',
    'Recorded requests': 'Zapisane żądania',
    'Recorded Decisions': 'Zapisane Decyzje',
    'Request-weighted mean': 'Średnia ważona liczbą żądań',
    'Failed requests': 'Nieudane żądania',
    'Traffic over time': 'Ruch w czasie',
    'Input throughput': 'Przepustowość wejścia',
    'Request rate': 'Tempo żądań',
    'One-minute buckets': 'Przedziały jednominutowe',
    'Model activity': 'Aktywność modeli',
    'in flight': 'w toku',
    'queued': 'w kolejce',
    'Endpoint traffic': 'Ruch według endpointów',
    'Requests by endpoint in the selected window.': 'Żądania według endpointów w wybranym okresie.',
    'Endpoint': 'Endpoint',
    'No traffic recorded yet.': 'Nie zarejestrowano jeszcze ruchu.',
    'Live endpoint attribution is not recorded by the current API.': 'Obecne API nie rejestruje liczników według endpointów.',
    'Historical traffic is not recorded by the current API.': 'Obecne API nie rejestruje historii natężenia ruchu.',
    'Switch to Demo data to compare the populated dashboard layout.': 'Przełącz na dane demonstracyjne, aby obejrzeć wypełniony pulpit.',
    'Input tokens processed per second, not generated output tokens.': 'Przetworzone tokeny wejściowe na sekundę, nie wygenerowane tokeny wyjściowe.',
    'Live counters exclude HTTP requests rejected before inference.': 'Rzeczywiste liczniki nie obejmują żądań HTTP odrzuconych przed inferencją.',
    'Live counters unavailable': 'Rzeczywiste liczniki są niedostępne',
    'Waiting for counters': 'Oczekiwanie na liczniki',
    'Collecting rate': 'Zbieranie danych o tempie',
    'Counter reset — collecting a new interval': 'Liczniki zresetowano — zbieranie nowego interwału',
    'Live status unavailable': 'Stan bieżący jest niedostępny',
    'Queue counters unavailable': 'Liczniki kolejki są niedostępne',
  });
  const q = selector => document.querySelector(selector);
  const number = (value, digits=0) => value == null ? '—' : new Intl.NumberFormat(LANG, {maximumFractionDigits:digits}).format(value);
  let source = 'demo', minutes = 15, metric = 'tokens';
  let liveStats = null, liveStatus = null, previous = null, rates = null, error = null, reset = false;
  const endpoints = [
    {path:'/v1/systemone', base:180, tokens:640, mean:128, p95:218},
    {path:'/v1/decisions', base:96, tokens:820, mean:164, p95:276},
    {path:'/api/decide', base:34, tokens:510, mean:142, p95:246},
    {path:'/mcp', base:20, tokens:730, mean:182, p95:310},
  ];
  // A fixed sixty-minute fixture makes time-window and metric switches meaningful without fake live animation.
  const buckets = Array.from({length:60}, (_, index) => {
    const wave = .7 + index / 110 + Math.sin(index * .62) * .16;
    const routes = endpoints.map((endpoint, route) => {
      const requests = Math.max(0, Math.round(endpoint.base * wave));
      return {requests, tokens:requests * endpoint.tokens, errors:(index + route * 3) % 11 === 0 ? 1 : 0};
    });
    return {routes, requests:routes.reduce((sum,row)=>sum+row.requests,0), tokens:routes.reduce((sum,row)=>sum+row.tokens,0)};
  });

  PAGES.dashboard = ['00 / DASHBOARD', 'Dashboard', 'Throughput, request traffic and serving health.'];
  const nav = document.createElement('button');
  nav.type = 'button';
  nav.dataset.view = 'dashboard';
  nav.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3v18h18M7 14l4-5 4 3 5-7"/></svg>${t('Dashboard')}`;
  nav.onclick = () => showView('dashboard', true);
  q('.side-nav').prepend(nav);
  const dashboard = document.createElement('section');
  dashboard.id = 'dashboard';
  dashboard.className = 'view prototype-traffic-dashboard';
  dashboard.dataset.page = 'dashboard';
  dashboard.hidden = true;
  dashboard.innerHTML = `<div class="prototype-traffic-toolbar">
      <div class="prototype-data-source" role="group" aria-label="${t('Traffic data source')}">
        <button type="button" data-traffic-source="demo" aria-pressed="true">${t('Demo data')}</button>
        <button type="button" data-traffic-source="live" aria-pressed="false">${t('Live counters')}</button>
      </div>
      <select class="select" id="prototype-traffic-window" aria-label="${t('Traffic window')}">
        <option value="15">${t('Last 15 minutes')}</option><option value="60">${t('Last hour')}</option>
      </select>
    </div>
    <div class="prototype-data-notice" role="status" id="prototype-traffic-source-note"></div>
    <div id="prototype-traffic-content"></div>`;
  q('.page-header').after(dashboard);

  function totals(models) {
    return Object.values(models).reduce((sum,row) => ({
      requests:sum.requests + row.requests,
      tokens:sum.tokens + row.input_tokens,
      errors:sum.errors + row.errors,
      latency:sum.latency + (row.avg_latency_ms || 0) * row.requests,
    }), {requests:0,tokens:0,errors:0,latency:0});
  }
  function demo() {
    const series = buckets.slice(-minutes);
    const rows = endpoints.map((endpoint,index) => {
      const sum = series.reduce((sum,bucket)=>({requests:sum.requests + bucket.routes[index].requests,
        tokens:sum.tokens + bucket.routes[index].tokens,errors:sum.errors + bucket.routes[index].errors}), {requests:0,tokens:0,errors:0});
      return {...endpoint,...sum};
    });
    const requests = rows.reduce((sum,row)=>sum+row.requests,0);
    const tokens = rows.reduce((sum,row)=>sum+row.tokens,0);
    const errors = rows.reduce((sum,row)=>sum+row.errors,0);
    return {series,rows,requests,tokens,errors,tps:tokens/(minutes*60),rps:requests/(minutes*60),
      mean:rows.reduce((sum,row)=>sum+row.mean*row.requests,0)/requests,
      models:[
        {id:'basal-2.0-mini-MLX-8bit', requests:Math.round(requests*.72), input_tokens:Math.round(tokens*.78), state:'ready', running:2, queued:1},
        {id:'basal-2.0-mini', requests:requests-Math.round(requests*.72), input_tokens:tokens-Math.round(tokens*.78), state:'ready', running:1, queued:0},
      ]};
  }
  function chart(series) {
    const values = series.map(bucket=>(metric === 'tokens' ? bucket.tokens : bucket.requests)/60);
    const maximum = Math.max(...values,1) * 1.18;
    const points = values.map((value,index)=>`${48+index*604/(values.length-1)},${164-value/maximum*138}`);
    const path = points.map((point,index)=>`${index?'L':'M'}${point}`).join(' ');
    return `<svg class="prototype-traffic-chart" viewBox="0 0 680 204" role="img" aria-label="${t(metric==='tokens'?'Input throughput':'Request rate')} · ${t('Demo data')}">
      <defs><linearGradient id="prototype-traffic-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="var(--accent)" stop-opacity=".18"/><stop offset="100%" stop-color="var(--accent)" stop-opacity="0"/></linearGradient></defs>
      ${[0,.5,1].map(fraction=>`<line x1="48" x2="652" y1="${164-fraction*138}" y2="${164-fraction*138}" stroke="var(--line)" stroke-dasharray="3 5"/><text x="0" y="${168-fraction*138}" fill="var(--muted)">${number(maximum*fraction,metric==='tokens'?0:1)}</text>`).join('')}
      <path d="${path} L652,164 L48,164 Z" fill="url(#prototype-traffic-fill)"/>
      <path d="${path}" fill="none" stroke="var(--accent)" stroke-width="2.5" stroke-linejoin="round"/>
      ${[0,.5,1].map(fraction=>`<text x="${48+604*fraction}" y="194" text-anchor="${fraction===0?'start':fraction===1?'end':'middle'}" fill="var(--muted)">−${number(minutes*(1-fraction))} min</text>`).join('')}
    </svg>`;
  }
  function render() {
    const activity = dashboard.querySelector('.prototype-model-activity');
    const activityOpen = activity?.open;
    const activityFocused = activity?.firstElementChild === document.activeElement;
    const isDemo = source === 'demo';
    const data = isDemo ? demo() : null;
    const count = isDemo ? data : (liveStats && !error ? totals(liveStats.models || {}) : null);
    const status = error ? null : liveStatus || statusCache;
    const modelIds = new Set([...Object.keys(liveStats?.models || {}), ...(status?.models || []).map(model=>model.id)]);
    const models = isDemo ? data.models : error ? [] : [...modelIds].map(id=>{
      const loaded = status?.models?.find(model=>model.id===id);
      return {id,requests:0,input_tokens:0,...liveStats?.models?.[id],state:loaded?.state || 'unloaded'};
    });
    const loaded = isDemo ? data.models : status?.models || [];
    const ready = loaded.filter(model=>model.state==='ready');
    const queued = ready.every(model=>typeof (isDemo ? model.queued : model.health?.queued)==='number')
      ? ready.reduce((sum,model)=>sum+(isDemo?model.queued:model.health.queued),0) : null;
    const inFlight = isDemo ? 3 : status ? loaded.reduce((sum,model)=>sum+(model.inflight?.length || 0),0) : null;
    const mean = isDemo ? data.mean : count?.requests ? count.latency/count.requests : null;
    const tps = isDemo ? data.tps : error ? null : rates?.tps;
    const rps = isDemo ? data.rps : error ? null : rates?.rps;
    const rateNote = isDemo ? 'Window average' : reset ? 'Counter reset — collecting a new interval' : rates ? 'Since previous refresh' : 'Collecting rate';
    const errorRate = count?.requests ? count.errors/count.requests*100 : null;
    dashboard.dataset.trafficSource = source;
    q('#prototype-traffic-window').hidden = !isDemo;
    q('#prototype-traffic-source-note').textContent = t(isDemo ? 'Illustrative traffic — not measurements from this machine.' : error ? 'Live counters unavailable' : !liveStats ? 'Waiting for counters' : 'Rates: last refresh interval. Counters: all recorded Decisions.');
    q('#prototype-traffic-content').innerHTML = `<div class="prototype-kpi-grid">
      ${[
        ['Input tokens /s',number(tps),'tok/s',t(rateNote)],
        ['Requests /s',number(rps,2),'req/s',`${number(count?.requests)} ${t('Recorded requests')}`],
        ['Mean latency',number(mean),'ms',t('Request-weighted mean')],
        ['Error rate',number(errorRate,2),'%',`${number(count?.errors)} ${t('Failed requests')}`],
      ].map(([label,value,unit,note])=>`<section class="card prototype-kpi"><h2>${t(label)}</h2><div class="prototype-kpi-value">${value}<small>${unit}</small></div><p>${esc(note)}</p></section>`).join('')}
    </div>
    <div class="prototype-traffic-panels">
      <section class="card prototype-throughput"><div class="prototype-traffic-heading"><div><h2>${t('Traffic over time')}</h2><p>${t(isDemo?'One-minute buckets':'Recorded Decisions')}</p></div>
        <div class="prototype-chart-metric" role="group" aria-label="${t('Traffic over time')}">
          <button type="button" data-chart-metric="tokens" aria-pressed="${metric==='tokens'}" ${isDemo?'':'disabled'}>tok/s</button>
          <button type="button" data-chart-metric="requests" aria-pressed="${metric==='requests'}" ${isDemo?'':'disabled'}>req/s</button>
        </div></div>
        ${isDemo?chart(data.series):`<div class="prototype-traffic-empty"><strong>${t('Historical traffic is not recorded by the current API.')}</strong><p>${t('Switch to Demo data to compare the populated dashboard layout.')}</p></div>`}
      </section>
      <${document.body.dataset.design==='C'?'details':'aside'} class="card prototype-model-activity" ${activityOpen?'open':''}>
        <${document.body.dataset.design==='C'?'summary':'h2'}>${t('Model activity')}</${document.body.dataset.design==='C'?'summary':'h2'}>
        <div class="prototype-activity-counts"><span><b>${isDemo||status?ready.length:'—'}</b> ${t('ready')}</span><span><b>${number(inFlight)}</b> ${t('in flight')}</span><span ${queued==null?`title="${t('Queue counters unavailable')}"`:''}><b>${isDemo||status?number(queued):'—'}</b> ${t('queued')}</span></div>
        ${models.length?models.map(model=>`<div class="prototype-active-model"><div><span class="status-dot${model.state==='ready'?' ready':''}"></span><strong>${esc(shortName(model.id))}</strong></div><p>${number(model.requests)} ${t('Requests')} · ${number(model.input_tokens)} ${t('Input tokens')}</p></div>`).join(''):`<p class="muted">${t(error?'Live status unavailable':liveStats?'No traffic recorded yet.':'Waiting for counters')}</p>`}
      </${document.body.dataset.design==='C'?'details':'aside'}>
    </div>
    <section class="card prototype-endpoint-traffic"><div class="prototype-traffic-heading"><div><h2>${t('Endpoint traffic')}</h2><p>${t(isDemo?'Requests by endpoint in the selected window.':'Live endpoint attribution is not recorded by the current API.')}</p></div><span class="prototype-source-badge">${t(isDemo?'Demo data':'Live counters')}</span></div>
      ${isDemo?`<div class="prototype-endpoint-scroll"><table><thead><tr><th>${t('Endpoint')}</th><th>${t('Requests')}</th><th>req/s</th><th>p95 · ms</th><th>${t('Errors')}</th></tr></thead><tbody>
        ${data.rows.map(row=>`<tr><td><span class="prototype-method">POST</span><code>${row.path}</code></td><td>${number(row.requests)}</td><td>${number(row.requests/(minutes*60),2)}</td><td>${row.p95}</td><td>${number(row.errors)}</td></tr>`).join('')}
      </tbody></table></div>`:`<p class="prototype-endpoint-limit">${t('Switch to Demo data to compare the populated dashboard layout.')}</p>`}
    </section>
    <p class="prototype-metric-definition">${t('Input tokens processed per second, not generated output tokens.')}${isDemo?'':` ${t('Live counters exclude HTTP requests rejected before inference.')}`}</p>`;
    if (activityFocused) dashboard.querySelector('.prototype-model-activity > summary')?.focus({preventScroll:true});
  }

  dashboard.addEventListener('click', event => {
    const button = event.target.closest('button');
    if (button?.dataset.trafficSource) {
      source = button.dataset.trafficSource;
      dashboard.querySelectorAll('[data-traffic-source]').forEach(control=>control.setAttribute('aria-pressed',String(control.dataset.trafficSource===source)));
      render();
    } else if (button?.dataset.chartMetric) {
      metric = button.dataset.chartMetric;
      render();
      dashboard.querySelector(`[data-chart-metric="${metric}"]`).focus();
    }
  });
  q('#prototype-traffic-window').onchange = event => { minutes=Number(event.target.value);render(); };

  // Observe successful existing app polls: no extra timer, requests, writes or long-term history.
  const originalApi = api;
  api = async function(path, ...args) {
    let data;
    try { data = await originalApi(path, ...args); }
    catch (failure) {
      if (path === '/api/stats' || path === '/api/status') {error=failure.message;previous=null;rates=null;if(source==='live'&&!dashboard.hidden)render();}
      throw failure;
    }
    if (path === '/api/status') liveStatus=data;
    if (path === '/api/stats') {
      const current = {...totals(data.models || {}),time:performance.now()};
      reset = Boolean(previous && (current.requests<previous.requests || current.tokens<previous.tokens));
      rates = previous && !reset && current.time>previous.time
        ? {tps:(current.tokens-previous.tokens)*1000/(current.time-previous.time),rps:(current.requests-previous.requests)*1000/(current.time-previous.time)} : null;
      liveStats=data;previous=current;error=null;
      if (source==='live'&&!dashboard.hidden)render();
    }
    return data;
  };
  render();
  if (!location.hash) history.replaceState(null, '', '#dashboard');
  if (location.hash === '#dashboard') showView('dashboard');
  q('.side-nav').addEventListener('click',()=>{if(!dashboard.hidden)render();});
  window.addEventListener('hashchange',()=>{if(!dashboard.hidden)render();});
})();
