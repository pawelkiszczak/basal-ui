'use strict';

(() => {
  const entryFacts = (entry, answer) => {
    const facts = [];
    if (answer?.calibration != null) facts.push(`${t('Decision calibration status')}: ${esc(answer.calibration)}`);
    for (const key of ['calibration', 'calibration_file', 'selected_calibration', 'shipped_calibration']) {
      const value = entry?.[key];
      if (value != null && value !== '') {
        const label = key === 'calibration_file' ? 'Calibration file' : key === 'selected_calibration' ? 'Selected calibration' : key === 'shipped_calibration' ? 'Shipped calibration' : 'Served calibration';
        facts.push(`${t(label)}: <code>${esc(typeof value === 'object' ? JSON.stringify(value) : value)}</code>`);
      }
    }
    if (entry?.temperature_per_prim && Object.keys(entry.temperature_per_prim).length)
      facts.push(`${t('Served calibration temperatures')}: <code>${esc(JSON.stringify(entry.temperature_per_prim))}</code>`);
    return facts.length ? `<ul class="dt-facts">${facts.map(fact => `<li>${fact}</li>`).join('')}</ul>` : `<p class="muted">${t('No selected or shipped calibration details are exposed by this model.')}</p>`;
  };

  function evidenceRail(answer, q, entry) {
    const type = decisionBase(answer, q), cert = entry?.certified || {};
    const t1 = ['choice', 'noul', 'score'].includes(type) ? cert['0.01']?.[type] : null;
    const t5 = ['choice', 'noul', 'score'].includes(type) ? cert['0.05']?.[type] : null;
    const gap = certificateGap(answer, entry);
    const reportedConfidence = answer.confidence ?? answer.set_confidence;
    const confidence = reportedConfidence == null ? null : Number(reportedConfidence);
    const applicable = Number.isFinite(confidence) && !answer.deferred && typeof answer.max_error !== 'string' && !gap && answer.confidence != null && (t1 != null || t5 != null);
    const reasons = [];
    if (answer.deferred) reasons.push(t('This answer was deferred; thresholds do not change that decision.'));
    if (typeof answer.max_error === 'string') reasons.push(t('The requested max_error is not certified: {reason}', {reason: answer.max_error}));
    if (gap) reasons.push(gap);
    else if (!answer.deferred && typeof answer.max_error !== 'string' && applicable)
      reasons.push(t('The served calibration and answer scope are applicable; thresholds shown are model-reported values.'));
    else if (!answer.deferred && typeof answer.max_error !== 'string' && reportedConfidence == null)
      reasons.push(t('This answer does not report a confidence value.'));
    else if (!answer.deferred && typeof answer.max_error !== 'string' && answer.confidence == null)
      reasons.push(t('Individual confidence is not reported for this answer.'));
    else if (!answer.deferred && typeof answer.max_error !== 'string' && !Number.isFinite(confidence))
      reasons.push(t('The reported confidence is not a finite number.'));
    else if (!answer.deferred && typeof answer.max_error !== 'string' && !['choice', 'noul', 'score'].includes(type))
      reasons.push(t('No certified thresholds are available for this answer type.'));
    else if (!answer.deferred && typeof answer.max_error !== 'string' && t1 == null && t5 == null)
      reasons.push(t('No certified thresholds are reported for this model and answer type.'));
    const reason = reasons.join(' ');
    const status = applicable ? t('Thresholds apply to this answer') : t('Threshold applicability not established');
    const thresholds = `<dl class="dt-thresholds"><div><dt>${t('1% error threshold')}</dt><dd>${t1 == null ? t('Not reported') : esc(Number(t1).toFixed(4))}</dd></div><div><dt>${t('5% error threshold')}</dt><dd>${t5 == null ? t('Not reported') : esc(Number(t5).toFixed(4))}</dd></div></dl>`;
    return `<aside class="dt-evidence" aria-label="${t('Calibration evidence')}"><div class="dt-evidence-main"><h4>${t('Calibration evidence')}</h4><div class="dt-confidence"><span>${t('Confidence')}</span><strong>${confidence == null || !Number.isFinite(confidence) ? '—' : esc(pct(confidence))}</strong></div>${thresholds}<p class="dt-applicability ${applicable ? 'is-applicable' : ''}"><strong>${status}</strong>${reason ? `<span>${esc(reason)}</span>` : ''}</p></div><details class="dt-calibration"><summary>${t('Active calibration facts')}</summary>${entryFacts(entry, answer)}</details></aside>`;
  }

  function answerPanel(name, answer, q, entry) {
    q ||= {};
    if (!answer || typeof answer !== 'object') return `<article class="answer dt-answer"><h3>${esc(name)}</h3><div class="dt-answer-layout"><div class="dt-answer-main"><pre>${esc(JSON.stringify(answer, null, 2))}</pre></div>${evidenceRail({}, q, entry)}</div></article>`;
    const type = answer.type || q.type || 'unknown', view = RESULT_VIEWS[type];
    const body = view ? view(answer, q) : answer.probabilities && typeof answer.probabilities === 'object'
      ? decisionView(name, answer, q) : `<pre>${esc(JSON.stringify(answer, null, 2))}</pre>`;
    return `<article class="answer dt-answer"><h3><span>${esc(name)}</span><span class="chip">${esc(type)}</span>${answer.experimental ? `<span class="badge experimental">${t('experimental')}</span>` : ''}</h3><div class="dt-answer-layout"><div class="dt-answer-main">${safetyStrip(answer, q, entry)}${thinkBlock(answer)}${body}</div>${evidenceRail(answer, q, entry)}</div></article>`;
  }

  let sequence = 0, ready = [], readySignature = '', running = false;
  const modelById = id => ready.find(model => model.id === id);
  const requestMedia = request => Array.isArray(request.media) ? request.media : [];
  const displayRequest = request => JSON.stringify(request, function (key, value) {
    return key === 'data' && (this?.type === 'image' || this?.type === 'audio') && typeof value === 'string'
      ? `[${this.type} data omitted from display; ${value.length} characters]` : value;
  }, 2);


  function renderResults(data, request, entry, mediaSnapshot) {
    const answers = data?.answers || data || {};
    const answerHtml = Object.entries(answers).map(([name, answer]) => `<div class="dt-result"><p class="dt-used">${t('Request')}: <code>${esc(name)}</code> · ${t('Model')}: <code>${esc(request.model)}</code></p>${answerPanel(name, answer, request.questions?.[name] || {}, entry)}</div>`).join('');
    const spans = typeof request.state === 'string' ? sourceSpans(answers, request) : [];
    const u = data?.usage, mediaUsage = u?.media ? ` · ${t('Media')}: ${esc(u.media.images ?? 0)} ${t('images')}, ${esc(u.media.audio ?? 0)} ${t('audio')}, ${esc(u.media.media_tokens ?? 0)} ${t('tokens')}` : '';
    const thinkUsage = u?.think_questions != null ? ` · ${t('Thought on')}: ${esc(u.think_questions)} · ${t('Output tokens')}: ${esc(u.output_tokens ?? 0)}` : '';
    const extraUsage = [['branches', u?.branches], ['second_order_branches', u?.second_order_branches], ['lane', u?.lane]].filter(([, value]) => value != null).map(([key, value]) => `${key}: ${esc(value)}`).join(' · ');
    const usage = u ? `<p class="answer-meta">${t('Latency')}: ${esc(u.latency_ms ?? '—')} ms · ${t('Input tokens')}: ${esc(u.input_tokens ?? '—')} · ${t('Questions')}: ${esc(u.questions ?? '—')}${extraUsage ? ` · ${extraUsage}` : ''}${mediaUsage}${thinkUsage}</p>` : '';
    const meter = u && entry?.context ? `<p class="answer-meta">${t('Longest prompt')} ${esc(u.longest_prompt_tokens ?? '—')} / ${esc(entry.context.limit ?? '—')} ${t('tokens')} · ${t('policy')} ${esc(entry.context.overflow)}</p>` : '';
    return `<div class="dt-result-body"><details class="dt-request-used"><summary>${t('Exact request sent to {model}', {model: esc(request.model)})}</summary><pre>${esc(displayRequest(request))}</pre></details>${resultBanners(entry, u)}${usage}${meter}${answerHtml || `<p class="empty">${t('No answers returned.')}</p>`}${spans.length ? renderSource(request.state, spans) : ''}${imageSources(answers, request, mediaSnapshot)}<details class="dt-raw"><summary>${t('Raw response')}</summary><pre>${esc(JSON.stringify(data, null, 2))}</pre></details></div>`;
  }

  function mount() {
    const playground = $('#playground .playground-shell');
    if (!playground || $('#decisionCompare')) return;
    playground.insertAdjacentHTML('afterend', `<section id="decisionCompare" class="card dt-compare" aria-labelledby="dt-title"><header class="dt-head"><div><div class="section-index">${t('REAL MODEL COMPARISON')}</div><h2 id="dt-title">${t('Compare loaded models')}</h2><p class="muted">${t('Run the current Playground request against two ready models using the same frozen input.')}</p></div></header><div class="dt-controls"><label><span>${t('First model')}</span><select class="select dt-model" aria-label="${t('First comparison model')}"></select></label><label><span>${t('Second model')}</span><select class="select dt-model" aria-label="${t('Second comparison model')}"></select></label><button type="button" class="primary" id="dt-run">${t('Compare models')}</button></div><p class="dt-error error-box" role="alert" hidden></p><div class="dt-prompt" hidden></div><div class="dt-grid" aria-live="polite"></div></section>`);
    $('#dt-run').addEventListener('click', compare);
    $('#decisionCompare').addEventListener('change', event => {
      if (event.target.matches('.dt-model') && !running) {
        sequence++;
        clearCompareOutput();
        reconcileSelects();
      }
    });
  }

  function clearCompareOutput() {
    const grid = $('#decisionCompare .dt-grid');
    if (grid) grid.replaceChildren();
    $('#decisionCompare .dt-prompt').hidden = true;
  }
  function setError(message) {
    const node = $('#decisionCompare .dt-error');
    node.textContent = message;
    node.hidden = !message;
  }
  function reconcileSelects() {
    const selects = [...document.querySelectorAll('#decisionCompare .dt-model')];
    if (!selects.length) return;
    const old = selects.map(select => select.value), focused = selects.indexOf(document.activeElement);
    for (const [index, select] of selects.entries()) {
      if (select.dataset.modelSignature !== readySignature) {
        select.innerHTML = ready.map(model => `<option value="${esc(model.id)}">${esc(model.id)}</option>`).join('') || `<option value="">${t('No model loaded')}</option>`;
        select.dataset.modelSignature = readySignature;
      }
      const first = ready.some(model => model.id === old[0]) ? old[0] : ready.some(model => model.id === selectedModel) ? selectedModel : ready[0]?.id || '';
      const next = index === 0
        ? (ready.some(model => model.id === old[index]) ? old[index] : first)
        : ready.some(model => model.id === old[index] && old[index] !== first) ? old[index] : ready.find(model => model.id !== first)?.id || '';
      select.value = next || '';
      select.disabled = ready.length < 2 || running;
    }
    $('#dt-run').disabled = ready.length < 2 || running;
    if (focused >= 0) selects[focused].focus();
  }

  async function compare() {
    const run = ++sequence, button = $('#dt-run'), grid = $('#decisionCompare .dt-grid');
    setError('');
    let base;
    try {
      const jsonMode = $('#editJsonToggle').checked;
      base = jsonMode ? JSON.parse($('#requestJson').value) : buildRequest();
      if (!base || typeof base !== 'object' || Array.isArray(base)) throw new Error(t('The request must be a JSON object.'));
      if (!jsonMode) {
        const invalid = [...document.querySelectorAll('.question')].filter(card => card._error);
        if (invalid.length) throw new Error(invalid.map(card => `${$('.q-name', card).value || t('question')}: ${card._error}`).join('\n'));
      }
    } catch (error) { setError(error.message); return; }
    const [first, second] = [...document.querySelectorAll('#decisionCompare .dt-model')].map(select => select.value), models = [first, second].map(modelById);
    if (!models[0] || !models[1] || first === second) { setError(t('Choose two different ready models.')); return; }
    const mediaTypes = [...new Set(requestMedia(base).map(media => media?.type).filter(type => type === 'image' || type === 'audio'))];
    const unsupported = models.map(model => ({id: model.id, types: mediaTypes.filter(type => !(model.media || []).includes(type))})).filter(item => item.types.length);
    if (unsupported.length) {
      setError(t('The selected models cannot all accept this request media: {models}. Choose two models that support every listed media type, or remove the media.', {models: unsupported.map(item => `${item.id} (${mediaTitle(item.types)})`).join('; ')}));
      return;
    }
    const evidenceQuestions = requestMedia(base).length ? Object.entries(base.questions || {}).filter(([, q]) => q?.evidence).map(([name]) => name) : [];
    if (evidenceQuestions.length) { setError(t('Evidence spans are read from state text and cannot be used with media: {questions}.', {questions: evidenceQuestions.join(', ')})); return; }
    let snapshot;
    try { snapshot = structuredClone(base); } catch (error) { setError(error.message); return; }
    delete snapshot.model;
    const mediaSnapshot = requestMedia(snapshot).map(media => {
      const item = selectedMedia.find(current => current.data === media?.data);
      return item ? {kind: item.kind, data: item.data, file: item.file, elements: item.elements ? structuredClone(item.elements) : undefined} : null;
    });
    const entries = Object.fromEntries(models.map(model => [model.id, structuredClone(modelEntry(model.id))]));
    const prompt = $('#decisionCompare .dt-prompt');
    prompt.hidden = false;
    prompt.innerHTML = `<strong>${t('Frozen request snapshot')}</strong><pre>${esc(displayRequest(snapshot))}</pre>`;
    running = true;
    reconcileSelects();
    grid.innerHTML = models.map(model => `<article class="dt-model-result" data-model="${esc(model.id)}" data-running="true"><h3>${esc(model.id)}</h3><p class="dt-used">${t('This model will receive the frozen request above.')}</p><p class="muted">${t('Running…')}</p></article>`).join('');
    try {
      const settled = await Promise.all(models.map(async model => {
        const request = structuredClone(snapshot); request.model = model.id;
        try { return {model, request, data: await api('/api/decide', {method: 'POST', body: JSON.stringify(request)})}; }
        catch (error) { return {model, request, error}; }
      }));
      if (run !== sequence) return;
      grid.innerHTML = settled.map(result => `<article class="dt-model-result" data-model="${esc(result.model.id)}">${result.error
        ? `<h3>${esc(result.model.id)}</h3><p class="dt-used">${t('Request sent')}: <code>${esc(result.model.id)}</code></p><details class="dt-request-used"><summary>${t('Exact request sent to {model}', {model: esc(result.model.id)})}</summary><pre>${esc(displayRequest(result.request))}</pre></details><div class="error-box" role="alert">${esc(result.error.message || String(result.error))}</div>`
        : `<h3>${esc(result.model.id)}</h3>${renderResults(result.data, result.request, entries[result.model.id], mediaSnapshot)}`}</article>`).join('');
    } catch (error) {
      if (run === sequence) setError(error.message || String(error));
    } finally {
      if (run === sequence) {
        running = false;
        reconcileSelects();
      }
    }
  }

  function refreshModels(models) {
    mount();
    const nextReady = Array.isArray(models) ? models.filter(model => model?.id && model.state === 'ready') : [];
    const signature = JSON.stringify(nextReady.map(model => [model.id, model.media || []]));
    if (signature !== readySignature) {
      readySignature = signature;
      sequence++;
      running = false;
      const grid = $('#decisionCompare .dt-grid');
      if (grid) grid.replaceChildren();
      $('#decisionCompare .dt-prompt').hidden = true;
    }
    ready = nextReady;
    reconcileSelects();
  }

  window.DecisionTools = {answerPanel, refreshModels};
  document.addEventListener('DOMContentLoaded', () => { mount(); refreshModels(readyModels()); });
})();
