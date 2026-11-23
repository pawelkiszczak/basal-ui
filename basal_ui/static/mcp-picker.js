'use strict';

function stripMcpMetadata(request) {
  const copy = structuredClone(request);
  if (copy.questions && typeof copy.questions === 'object' && !Array.isArray(copy.questions)) {
    for (const q of Object.values(copy.questions)) if (q && typeof q === 'object' && !Array.isArray(q)) delete q._mcp_source;
  }
  return copy;
}
function toolDefinition(tool) { return tool && Object.hasOwn(tool, 'function') ? tool.function : tool; }
function toolName(tool) { return toolDefinition(tool)?.name; }
function validTool(tool) {
  const f = toolDefinition(tool), name = f?.name;
  if (!tool || typeof tool !== 'object' || Array.isArray(tool) || (tool.type !== undefined && tool.type !== 'function') || typeof name !== 'string' || !name || /\s/.test(name) || (f.description != null && typeof f.description !== 'string')) return false;
  const key = ['parameters','inputSchema','input_schema'].find(k => Object.hasOwn(f,k));
  const schema = key === undefined ? null : f[key];
  if (schema == null) return true;
  if (typeof schema !== 'object' || Array.isArray(schema) || (schema.type !== undefined && schema.type !== 'object')) return false;
  const props = schema.properties ?? {}, required = schema.required ?? [];
  return props && typeof props === 'object' && !Array.isArray(props) && Object.values(props).every(v => typeof v === 'boolean' || (v && typeof v === 'object' && !Array.isArray(v))) && Array.isArray(required) && required.every(x => typeof x === 'string' && Object.hasOwn(props,x)) && new Set(required).size === required.length;
}
function toolLimit(model) {
  const format = modelEntry(model)?.prompt_format;
  return format === 1 || format === '1' ? 10 : format === 2 || format === '2' ? 255 : null;
}
function toolBudgetError(request, model) {
  const limit = toolLimit(model);
  if (!limit || !request?.questions || typeof request.questions !== 'object') return '';
  for (const q of Object.values(request.questions)) {
    if (q?.type !== 'tool' || !Array.isArray(q.tools)) continue;
    const extras = Array.isArray(q.extra) ? q.extra.length : q.multi ? 0 : 3;
    if (q.tools.length + extras > limit) return t('Tools and other options exceed the active model limit of {limit}.', {limit});
  }
  return '';
}

(() => {
  const dialog = document.createElement('dialog');
  dialog.className = 'confirm-dialog mcp-dialog';
  dialog.setAttribute('aria-labelledby', 'mcp-title');
  dialog.innerHTML = `<h2 id="mcp-title">${t('Import MCP tools')}</h2>
    <label for="mcp-url">${t('MCP server URL')}</label><input id="mcp-url" type="url" autocomplete="off">
    <label for="mcp-token">${t('Bearer token (optional)')}</label><input id="mcp-token" type="password" autocomplete="off">
    <div class="mcp-actions"><button type="button" id="mcp-import">${t('Import or refresh')}</button><button type="button" id="mcp-all">${t('Select all')}</button><button type="button" id="mcp-none">${t('Select none')}</button></div>
    <label for="mcp-k">${t('Shortlist to K')}</label><input id="mcp-k" type="number" min="1" max="255" value="40"><button type="button" id="mcp-shortlist">${t('Shortlist')}</button>
    <p id="mcp-status" role="status" aria-live="polite"></p><p id="mcp-error" role="alert"></p><div id="mcp-list"></div>
    <div class="dialog-actions"><button type="button" id="mcp-apply">${t('Apply selected tools')}</button><button type="button" id="mcp-close">${t('Cancel')}</button></div>`;
  document.body.append(dialog);
  const get = id => dialog.querySelector('#mcp-' + id);
  let state = null, sequence = 0;
  function origin(url) { try { return new URL(url).origin; } catch { return null; } }
  function invalidate(clearToken=false) { sequence++; state?.controller?.abort(); if (state) state.controller = null; if (clearToken) get('token').value = ''; get('import').disabled = get('shortlist').disabled = false; }
  function close() { const opener = state?.opener; invalidate(true); dialog.close(); state = null; if (opener?.isConnected) opener.focus(); }
  function current(s) {
    return state === s && !s.stale && dialog.open && s.card.isConnected && $('.q-type', s.card).value === 'tool' &&
      $('.q-name', s.card).value === s.name && $('[data-f="tools"]', s.card)?.value === s.toolsText &&
      $('.q-instructions-input', s.card).value === s.instructions && $('#playgroundModel').value === s.model &&
      !$('#editJsonToggle').checked && get('url').value === s.url &&
      JSON.stringify([...s.card.querySelectorAll('[data-extra]')].map(x => x.checked)) === s.extras;
  }
  function merged() {
    if (state.ranked) return state.ranked.filter(item => state.selected.has(toolName(item)));
    const out = [...state.original], positions = new Map(out.map((x, i) => [toolName(x), i]));
    for (const item of state.items.filter(x => state.selected.has(x.name))) {
      const index = positions.get(item.name);
      if (index === undefined) { positions.set(item.name, out.length); out.push(item); } else out[index] = item;
    }
    return out;
  }
  function render() {
    if (!state) return;
    const displayed = state.ranked || state.items;
    get('list').innerHTML = displayed.map((item, i) => {const def=toolDefinition(item), name=toolName(item); return `<label class="mcp-item"><input type="checkbox" data-index="${i}" ${state.selected.has(name) ? 'checked' : ''}><span><strong>${esc(name)}</strong><br>${esc(def.description || '')}</span></label>`;}).join('');
    const total = merged().length, extras = JSON.parse(state.extras).filter(Boolean).length, limit = toolLimit(state.model);
    const clashes = merged().some(item => [...state.card.querySelectorAll('[data-extra]:checked')].some(extra => extra.dataset.extra === toolName(item)));
    get('status').textContent = t('{selected} selected, {total} merged tools.', {selected:state.selected.size,total}) + ' ' +
      (limit ? t('{remaining} option slots remaining.', {remaining:limit-total-extras}) : t('Active prompt format is unknown; option limit cannot be checked.')) +
      (total > 50 ? ' ' + t('More than 50 tools: shortlist first.') : '') +
      (limit !== null && total + extras > limit ? ' ' + t('Tools and other options exceed the active model limit of {limit}.', {limit}) : '');
    get('apply').disabled = !(state.ranked || state.items).length || clashes || (limit !== null && total + extras > limit);
    if (clashes) error(t('A tool name clashes with another enabled option.'));
  }
  function error(message) { get('error').textContent = message; }
  async function operation(path, body, done) {
    const s = state; invalidate(); const op = ++sequence, controller = new AbortController(); s.controller = controller;
    get('import').disabled = get('shortlist').disabled = true; get('status').textContent = t('Working…'); error('');
    try {
      const result = await api(path, {method:'POST', body:JSON.stringify(body), signal:controller.signal});
      if (op === sequence && current(s)) { done(result); render(); }
      else if (op === sequence && state === s) error(t('Question changed. Close and reopen the picker.'));
    } catch (e) { if (op === sequence && e.name !== 'AbortError') error(e.message); }
    finally { if (op === sequence && state === s) { s.controller = null; get('import').disabled = get('shortlist').disabled = false; render(); } }
  }
  document.addEventListener('click', event => {
    const button = event.target.closest('.mcp-open'); if (!button) return;
    const card = button.closest('.question'), area = $('[data-f="tools"]', card), text = area.value;
    let original; try { original = text.trim() ? JSON.parse(text) : []; if (!Array.isArray(original) || original.some(x => !validTool(x)) || new Set(original.map(toolName)).size !== original.length) throw Error(); }
    catch { toast(t('Fix the tools JSON before importing.'), 'error'); return; }
    const source = card._extra?._mcp_source;
    state = {card, opener:button, name:$('.q-name',card).value, toolsText:text, instructions:$('.q-instructions-input',card).value, model:$('#playgroundModel').value, extras:JSON.stringify([...card.querySelectorAll('[data-extra]')].map(x=>x.checked)), original, items:[], selected:new Set(), remembered:source, imported:false, ranked:null};
    get('url').value = typeof source?.url === 'string' ? source.url : ''; state.url = get('url').value;
    get('token').value = ''; get('k').value = '40'; error(''); render(); dialog.showModal(); get('url').focus();
  });
  document.addEventListener('input', event => { if (state && (state.card.contains(event.target) || event.target.matches('#playgroundModel, #requestJson'))) { state.stale = true; invalidate(true); error(t('Question changed. Close and reopen the picker.')); } });
  document.addEventListener('change', event => { if (state && (state.card.contains(event.target) || event.target.matches('#playgroundModel, #editJsonToggle, #examples'))) { state.stale = true; invalidate(true); error(t('Question changed. Close and reopen the picker.')); } });
  get('url').addEventListener('input', () => { invalidate(origin(state?.url) !== origin(get('url').value)); if (state) { state.url = get('url').value; state.items = []; state.selected.clear(); state.ranked = null; state.imported = false; render(); } });
  get('import').onclick = () => { const s=state, token=get('token').value, url=get('url').value; s.url=url; operation('/api/mcp/tools', {url,...(token ? {token} : {})}, result => { if (s.ranked) s.original=merged(); s.ranked=null; s.imported=true; s.items=result.tools; s.selected=new Set(s.remembered?.url === url && Array.isArray(s.remembered.selected) ? s.items.filter(x=>s.remembered.selected.includes(x.name)).map(x=>x.name) : s.items.map(x=>x.name)); }); };
  get('all').onclick = () => { invalidate(); state.selected = new Set((state.ranked || state.items).map(toolName)); render(); };
  get('none').onclick = () => { invalidate(); state.selected.clear(); render(); };
  get('list').onchange = event => { invalidate(); const item=(state.ranked || state.items)[Number(event.target.dataset.index)]; if (!item) return; const name=toolName(item); event.target.checked ? state.selected.add(name) : state.selected.delete(name); render(); };
  get('shortlist').onclick = () => { const s=state, k=Number(get('k').value), limit=toolLimit(s.model), extras=JSON.parse(s.extras).filter(Boolean).length; if (!Number.isInteger(k) || k<1 || k>255 || (limit && k>limit-extras)) return error(t('Choose K within the remaining option limit.')); const tools=merged(); operation('/api/tools/shortlist', {tools, query:s.instructions, k}, result => { s.ranked=result.tools; s.selected=new Set(result.tools.map(toolName)); }); };
  get('apply').onclick = () => { const s=state; if (!current(s)) return error(t('Question changed. Close and reopen the picker.')); const result=merged(), limit=toolLimit(s.model), extras=JSON.parse(s.extras).filter(Boolean).length; if (limit && result.length+extras>limit) return error(t('Tools and other options exceed the active model limit of {limit}.', {limit})); $('[data-f="tools"]',s.card).value=JSON.stringify(result,null,2); const names=new Set(result.map(toolName)); const source=s.imported?{url:s.url,selected:[...s.selected].filter(n=>s.items.some(item=>toolName(item)===n))}:s.remembered; if(source && typeof source.url==='string' && Array.isArray(source.selected)) s.card._extra._mcp_source={url:source.url,selected:source.selected.filter(n=>names.has(n))}; if (s.card._orig?.type === 'tool') { if(s.card._extra._mcp_source) s.card._orig.q._mcp_source=structuredClone(s.card._extra._mcp_source); else delete s.card._orig.q._mcp_source; } syncRequest(); close(); };
  get('close').onclick = close;
  dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
})();
