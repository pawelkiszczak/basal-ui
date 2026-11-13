/* Read-only Batch path inspector; loaded after app.js so the original controls keep their handlers. */
(() => {
  const boot = () => {
    const view = document.querySelector('#batch');
    const card = view?.querySelector(':scope > .card');
    if (!card || document.querySelector('#pathInspector')) return;
    const layout = document.createElement('div');
    layout.className = 'path-inspector-layout';
    card.parentNode.insertBefore(layout, card);
    layout.append(card);
    for (const [selector, title] of [['#batchModel','Model'],['#batchInput','Input JSONL'],['#batchOutput','Output JSONL']]) {
      const input = view.querySelector(selector), label = document.createElement('label');
      label.className = 'batch-path-field'; label.htmlFor = input.id;
      const text = document.createElement('span'); text.textContent = t(title);
      input.before(label); label.append(text,input);
    }
    const rail = document.createElement('aside');
    rail.id = 'pathInspector';
    rail.className = 'card path-inspector';
    rail.setAttribute('aria-labelledby', 'pathInspectorTitle');
    rail.innerHTML = `<div class="section-head"><h2 id="pathInspectorTitle">${esc(t('Path inspector'))}</h2><button type="button" id="checkBatchPaths">${esc(t('Check paths'))}</button></div><p class="muted">${esc(t('Checks happen on this computer. Checking does not change files.'))}</p><div class="path-check" data-kind="jsonl"><h3>${esc(t('Input JSONL'))}</h3><div class="path-result muted" role="status">${esc(t('Enter an input path to inspect it.'))}</div></div><div class="path-check" data-kind="output"><h3>${esc(t('Output JSONL'))}</h3><p class="muted">${esc(t('If this field is blank, Batch writes <stem>.answers.jsonl beside the input.'))}</p><div class="path-result muted" role="status">${esc(t('Enter an output path to check its folder and overwrite status.'))}</div></div><p class="path-caps muted">${esc(t('Inspection stops at 100,000 rows or 64 MiB; the first row is limited to 1 MiB. These are inspection limits, not Batch file limits.'))}</p>`;
    layout.append(rail);

    const sequence = new Map([['jsonl', 0], ['output', 0]]);
    const setMessage = (kind, message, cls = 'muted') => {
      const target = rail.querySelector(`[data-kind="${kind}"] .path-result`);
      target.className = `path-result ${cls}`;
      target.textContent = message;
    };
    const show = (kind, data) => {
      const target = rail.querySelector(`[data-kind="${kind}"] .path-result`);
      target.className = `path-result ${data.ok && !(kind === 'output' && data.overwrite) ? 'path-ok' : 'path-warning'}`;
      if (kind === 'output') {
        const failures = {dir_missing: t('Output folder does not exist.'), not_writable: t('Output location is not writable.'), is_dir: t('Output path is a directory.'), not_file: t('Output path is not a regular file.')};
        const facts = data.code ? (failures[data.code] || t('Path check failed.')) : data.overwrite ? t('Existing output file will be overwritten.') : t('Output folder is writable; no file will be overwritten.');
        target.innerHTML = `<p>${esc(facts)}</p><p class="mono">${esc(data.path)}</p>`;
        return;
      }
      const messages = {
        empty: t('Input file is empty.'), missing: t('Input file does not exist.'), is_dir: t('Input path is a directory, not a file.'),
        not_file: t('Input path is not a regular file.'), unreadable: t('Input file cannot be read.'), not_text: t('Input is not valid UTF-8 text.'),
        not_json: t('First non-empty line is not a JSON object.'), invalid_schema: t('First row has an invalid batch schema.'), unknown_format: t('First row is not a supported batch request.'),
        line_too_long: t('First non-empty row exceeds the 1 MiB inspection limit.'), scan_limit: t('No request row found within the inspection limit.'), invalid: t('This path cannot be resolved.'),
      };
      const text = data.code ? (messages[data.code] || t('Path check failed.')) : t('First non-empty row looks like a supported batch request.');
      let preview = data.first?.preview || '';
      try { preview = JSON.stringify(JSON.parse(preview),null,2); } catch {}
      target.innerHTML = `<p>${esc(text)}</p>${data.schema_error?`<p>${esc(data.schema_error)}</p>`:''}<p class="mono">${esc(data.path)}</p>${data.lines!=null?`<div class="chips"><span class="chip">${esc(tp(data.lines,'{n} non-empty row','{n} non-empty rows'))}${data.lines_capped?' '+esc(t('(count capped)')):''}</span>${data.first?`<span class="chip">${esc(t(data.first.format==='simple'?'Simple item':'System One request'))}</span><span class="chip">${esc(tp(data.first.questions,'{n} question','{n} questions'))}</span>`:''}</div>`:''}${preview?`<h4>${esc(t('First-row sample'))}</h4><pre>${esc(preview)}</pre>`:''}`;
    };
    const fields = [['jsonl', view.querySelector('#batchInput')], ['output', view.querySelector('#batchOutput')]];
    const check = async (kind, input) => {
      const path = input.value.trim(), id = sequence.get(kind) + 1;
      sequence.set(kind,id);
      if (!path) {
        setMessage(kind, kind==='jsonl'?t('Enter an input path to inspect it.'):t('Enter an output path to check its folder and overwrite status.'));
        return;
      }
      setMessage(kind,t('Checking path…'));
      try {
        const data = await api('/api/paths/check',{method:'POST',body:JSON.stringify({path,kind,...(kind==='jsonl'&&$('#batchPreset').value?{preset:$('#batchPreset').value}:{})})});
        if(id===sequence.get(kind)&&input.value.trim()===path)show(kind,data);
      } catch(error) {
        if(id===sequence.get(kind)&&input.value.trim()===path)setMessage(kind,error.message,'path-warning');
      }
    };
    ['#batchPreset','#batchLang'].forEach(selector=>$(selector).addEventListener('change',()=>{
      const input=view.querySelector('#batchInput');
      sequence.set('jsonl',sequence.get('jsonl')+1);
      if(input.value.trim())check('jsonl',input);
    }));
    fields.forEach(([kind,input])=>input.addEventListener('input',()=>{
      sequence.set(kind,sequence.get(kind)+1);
      setMessage(kind,t('Edit a path, then check again.'));
    }));
    $('#checkBatchPaths').onclick=async()=>{
      const button=$('#checkBatchPaths');button.disabled=true;
      try {await Promise.all(fields.map(([kind,input])=>check(kind,input)));}
      finally {button.disabled=false;}
    };
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, {once: true});
  else boot();
})();
