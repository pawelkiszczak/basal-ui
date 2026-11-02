'use strict';

window.RequestCode = (() => {
  const formats = [['curl','System One · curl'],['python','System One · Python'],['openai','OpenAI Decisions · curl'],['mcp','MCP']];
  let root = null, format = 'curl', snippet = '', connectionSignature = '';
  const shell = value => `'${String(value).replaceAll("'", "'\\''")}'`;
  function request() {
    const json = $('#editJsonToggle').checked;
    const body = json ? JSON.parse($('#requestJson').value) : buildRequest();
    if (!body || typeof body !== 'object' || Array.isArray(body)) throw new Error(t('The request must be a JSON object.'));
    if (!json) {
      const invalid = [...document.querySelectorAll('.question')].filter(card => card._error);
      if (invalid.length) throw new Error(invalid.map(card => `${$('.q-name',card).value}: ${card._error}`).join('\n'));
    }
    const model = $('#playgroundModel').value || body.model;
    if (!model) throw new Error(t('Choose a model before generating API examples.'));
    return {...body, model};
  }
  function python(value, pad = '') {
    if (value == null) return 'None';
    if (typeof value === 'boolean') return value ? 'True' : 'False';
    if (typeof value === 'number') return Number.isFinite(value) ? String(value) : 'None';
    if (typeof value === 'string') return JSON.stringify(value);
    const inner = pad + '    ';
    if (Array.isArray(value)) return value.length ? `[\n${value.map(item => inner + python(item,inner)).join(',\n')},\n${pad}]` : '[]';
    const entries = Object.entries(value).filter(([,item]) => item !== undefined);
    return entries.length ? `{\n${entries.map(([key,item]) => `${inner}${JSON.stringify(key)}: ${python(item,inner)}`).join(',\n')},\n${pad}}` : '{}';
  }
  function refuse(fields) {
    throw new Error(t('This protocol cannot preserve these request fields: {fields}. Use System One instead.', {fields:fields.join(', ')}));
  }
  function decisions(body) {
    const extra = Object.keys(body).filter(key => !['model','state','questions','media','detail'].includes(key));
    if (extra.length) refuse(extra);
    if (typeof body.state !== 'string') refuse(['state (structured)']);
    if (body.detail && body.detail !== 'default') refuse(['detail']);
    const media = body.media || [];
    if (!Array.isArray(media) || media.some(item => item?.type !== 'image')) refuse(['media (audio or invalid)']);
    const questions = Object.entries(body.questions || {}).map(([name,q]) => {
      const type = q.type || 'choice';
      if (!['choice','noul','score'].includes(type)) refuse([`${name}: ${type}`]);
      const unknown = Object.keys(q).filter(key => !['type','instructions','criteria','levels','option_keys'].includes(key));
      if (unknown.length) refuse(unknown.map(key => `${name}.${key}`));
      if (q.option_keys && q.option_keys !== 'show') refuse([`${name}.option_keys`]);
      if (typeof q.instructions !== 'string' || !q.instructions.trim()) throw new Error(t('OpenAI Decisions needs non-empty text instructions for every question.'));
      const result = {name,type:type === 'noul' ? 'predicate' : type,instructions:q.instructions};
      const criteria = q.criteria ?? q.levels ?? [];
      if (type === 'noul') { if (Object.keys(criteria).length) refuse([`${name}.criteria`]); return result; }
      const entries = Array.isArray(criteria)
        ? criteria.map((item,index) => [type === 'score' ? String(item) : item,null])
        : Object.entries(criteria);
      if (entries.some(([key]) => typeof key !== 'string')) refuse([`${name}.criteria`]);
      const limit = type === 'choice' ? 255 : 10;
      if (entries.length < 2 || entries.length > limit) throw new Error(t('OpenAI Decisions needs between 2 and {limit} options for {question}.', {limit,question:name}));
      result[type === 'choice' ? 'choices' : 'levels'] = entries.map(([key,description]) => ({[type === 'choice' ? 'value' : 'label']:key,...(description != null ? {description} : {})}));
      return result;
    });
    if (!questions.length || questions.length > 200) throw new Error(t('OpenAI Decisions needs between 1 and 200 questions.'));
    const input = media.length ? [{role:'user',content:[{type:'input_text',text:body.state},...media.map(item => {
      const selected = selectedMedia.find(current => current.kind === 'image' && current.data === item.data);
      return {type:'input_image',image_url:item.data.startsWith('data:') ? item.data : `data:${selected?.file?.type || 'image/png'};base64,${item.data}`};
    })]}] : body.state;
    return {model:body.model,input,questions};
  }
  const mcpAvailable = () => Boolean(serveData?.mcp_available && statusCache?.serving?.running && statusCache.serving.mcp);
  function toolCall(body) {
    if (!mcpAvailable()) throw new Error(t('Start the API server with MCP enabled to use this example.'));
    const routes = Boolean(statusCache.serving.mcp_models);  // engine 2.5.0: the tools take `model`
    const first = statusCache.serving.models?.[0];
    if (!routes && body.model !== first) throw new Error(t('MCP routes to {model}, not the selected model. Use System One to target the selected model.', {model:first || t('No model loaded')}));
    const extra = Object.keys(body).filter(key => !['model','state','questions','lang','facts'].includes(key));
    if (extra.length) refuse(extra);
    const args = {state:body.state,questions:body.questions};
    for (const key of ['lang','facts']) if (key in body) args[key] = body[key];
    if (routes) args.model = body.model;
    return {jsonrpc:'2.0',id:1,method:'tools/call',params:{name:'decide_many',arguments:args}};
  }
  function invalidate() {
    if (!root) return;
    snippet = '';
    $('#requestCodeText').textContent = '';
    $('#copyRequestCode').disabled = true;
    $('#requestCodeNote').textContent = t('Generate an example from the current request before copying.');
    $('#requestCodeNote').className = 'muted';
  }
  function setFormat(value) {
    format = value;
    root.querySelectorAll('[data-request-format]').forEach(button => {
      const active = button.dataset.requestFormat === value;
      button.setAttribute('aria-selected',String(active)); button.tabIndex = active ? 0 : -1;
    });
    $('#requestCodePanel').setAttribute('aria-labelledby',`request-code-${value}`);
    invalidate();
  }
  function connectionChanged() {
    if (!root) return;
    const next = JSON.stringify([serveData?.settings,statusCache?.serving?.url,statusCache?.serving?.require_key,statusCache?.serving?.mcp,statusCache?.serving?.running,statusCache?.serving?.models]);
    if (next === connectionSignature) return;
    connectionSignature = next;
    $('#request-code-mcp').disabled = !mcpAvailable();
    $('#requestMcpNote').hidden = mcpAvailable();
    if (format === 'mcp' && !mcpAvailable()) setFormat('curl');
    invalidate();
  }
  function generate() {
    invalidate();
    try {
      const body = request();
      if (!serveData) throw new Error(t('API server settings are unavailable. Open API server and retry.'));
      const endpoint = connectionEndpoint();
      const key = statusCache?.serving ? statusCache.serving.require_key : serveData.settings.require_key;
      const auth = key ? ' -H "Authorization: Bearer $BASAL_API_KEY"' : '';
      let note = statusCache?.serving?.running ? t('Uses the running API server configuration.') : t('Start the API server before running this example.');
      if (format === 'mcp') {
        snippet = JSON.stringify(toolCall(body),null,2);
        note = statusCache.serving.mcp_models
          ? t('Send this tools/call through an initialized MCP client at {url}. list_models names the loaded models; model selects one.', {url:`${endpoint}/mcp`})
          : t('Send this tools/call through an initialized MCP client at {url}. The gateway selects the first loaded model.', {url:`${endpoint}/mcp`});
      } else if (format === 'python') {
        snippet = `${key ? 'import os\n' : ''}import httpx\n\nresponse = httpx.post(\n    ${JSON.stringify(endpoint + '/v1/systemone')},\n    json=${python(body,'    ')},${key ? '\n    headers={"Authorization": "Bearer " + os.environ["BASAL_API_KEY"]},' : ''}\n    timeout=120,\n)\nresponse.raise_for_status()\nprint(response.json())`;
      } else {
        const exported = format === 'openai' ? decisions(body) : body;
        snippet = `curl --fail-with-body ${shell(endpoint + (format === 'openai' ? '/v1/decisions' : '/v1/systemone'))} -H 'Content-Type: application/json'${auth} \\\n  --data-binary @- <<'BASAL_REQUEST'\n${JSON.stringify(exported,null,2)}\nBASAL_REQUEST`;
      }
      $('#requestCodeText').textContent = snippet;
      $('#requestCodeNote').textContent = note + (key ? ` ${t('Set BASAL_API_KEY to an API key before running this example.')}` : '');
      $('#copyRequestCode').disabled = false;
    } catch (error) {
      $('#requestCodeNote').textContent = error.message;
      $('#requestCodeNote').className = 'error-box';
    }
  }
  function open() {
    if (!root) return;
    root.scrollIntoView({block:'start'});
    $('#generateRequestCode').focus();
    generate();
  }
  document.addEventListener('DOMContentLoaded', () => {
    root = document.createElement('section'); root.id = 'requestCode'; root.className = 'card request-code';
    root.setAttribute('aria-labelledby','requestCodeTitle');
    root.innerHTML = `<div class="section-head"><div><h2 id="requestCodeTitle">${t('API examples')}</h2><p class="muted">${t('Export the current request without sending a Decision.')}</p></div><button type="button" id="generateRequestCode">${t('Generate example')}</button></div><div class="tabs request-code-tabs" role="tablist" aria-label="${t('API example protocol')}">${formats.map(([key,label],index) => `<button type="button" role="tab" id="request-code-${key}" data-request-format="${key}" aria-controls="requestCodePanel" aria-selected="${index === 0}" tabindex="${index === 0 ? 0 : -1}">${esc(t(label))}</button>`).join('')}</div><div id="requestCodePanel" role="tabpanel" aria-labelledby="request-code-curl"><p id="requestCodeNote" class="muted" role="status"></p><pre><code id="requestCodeText"></code></pre><button type="button" id="copyRequestCode" disabled>${t('Copy example')}</button></div><p id="requestMcpNote" class="muted">${t('Start the API server with MCP enabled to use this example.')} <a href="#api">${t('API server')} →</a></p>`;
    $('#playground .playground-shell').insertAdjacentElement('afterend',root);
    const tabs = [...root.querySelectorAll('[data-request-format]')];
    tabs.forEach((button,index) => {
      button.onclick = () => { setFormat(button.dataset.requestFormat); generate(); };
      button.onkeydown = event => {
        if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key) || event.altKey || event.ctrlKey || event.metaKey) return;
        event.preventDefault();
        const enabled = tabs.filter(tab => !tab.disabled), current = enabled.indexOf(button);
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? enabled.length - 1 : (current + (event.key === 'ArrowRight' ? 1 : -1) + enabled.length) % enabled.length;
        enabled[next].focus(); enabled[next].click();
      };
    });
    $('#generateRequestCode').onclick = generate;
    $('#copyRequestCode').onclick = () => { if (snippet) copyText(snippet,t('Code copied')); };
    $('#requestJson').addEventListener('input',invalidate);
    connectionChanged(); invalidate();
  });
  return {invalidate,connectionChanged,open};
})();
