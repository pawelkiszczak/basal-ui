/* Scheduled Batch uses the existing status refresh and Batch path inspector. */
(() => {
  const boot = () => {
    const view = $('#batch'), manual = view.querySelector('.path-inspector-layout');
    if (!manual) return;
    const tabs = document.createElement('div');
    tabs.className = 'tabs'; tabs.setAttribute('role', 'tablist'); tabs.setAttribute('aria-label', t('Batch'));
    tabs.innerHTML = `<button type="button" role="tab" id="batchRunTab" aria-selected="true" aria-controls="batchRunPanel">${esc(t('Batch run'))}</button><button type="button" role="tab" id="batchSchedulesTab" aria-selected="false" aria-controls="schedulePanel" tabindex="-1">${esc(t('Schedules'))}</button>`;
    manual.before(tabs); manual.id = 'batchRunPanel'; manual.setAttribute('role', 'tabpanel'); manual.setAttribute('aria-labelledby', 'batchRunTab');
    const panel = document.createElement('section'); panel.id = 'schedulePanel'; panel.className = 'card';
    panel.hidden = true; panel.setAttribute('role', 'tabpanel'); panel.setAttribute('aria-labelledby', 'batchSchedulesTab');
    panel.innerHTML = `<h2 id="schedulePanelTitle" tabindex="-1">${esc(t('Scheduled Batch'))}</h2><p class="muted">${esc(t('Run now ignores pause and timing, but still respects processed files and overwrite protection.'))}</p>
      <div id="scheduleList"></div><h3 id="scheduleFormTitle">${esc(t('Create schedule'))}</h3>
      <form id="scheduleForm" class="schedule-form">
      <label>${esc(t('Model'))}<select id="scheduleModel" required></select></label>
      <label>${esc(t('Input folder'))}<input id="scheduleInputDir" type="text" required autocomplete="off"></label>
      <label>${esc(t('File pattern'))}<input id="schedulePattern" value="*.jsonl" required></label>
      <label>${esc(t('Output folder'))}<input id="scheduleOutputDir" type="text" required autocomplete="off"></label>
      <label>${esc(t('Concurrency'))}<input id="scheduleConcurrency" type="number" min="1" max="256" step="1" value="16" required></label>
      <label>${esc(t('Interval'))}<select id="scheduleInterval"><option value="minutes">${esc(t('Every N minutes'))}</option><option value="hours">${esc(t('Every N hours'))}</option><option value="daily">${esc(t('Daily at'))}</option></select></label>
      <label id="scheduleEveryLabel">${esc(t('Every'))}<input id="scheduleEvery" type="number" min="1" max="525600" step="1" value="60"></label>
      <label id="scheduleDailyLabel" hidden>${esc(t('Local time'))}<input id="scheduleDaily" type="time" value="09:00"></label>
      <label class="schedule-check"><input id="scheduleOverwrite" type="checkbox"> ${esc(t('Allow overwriting output'))}</label>
      <label class="schedule-check"><input id="scheduleCatchUp" type="checkbox"> ${esc(t('Catch up missed daily run'))}</label>
      <div class="controls"><button type="button" id="scheduleCheck">${esc(t('Check folders'))}</button><button type="submit" class="primary">${esc(t('Save schedule'))}</button><button type="button" id="scheduleCancel" hidden>${esc(t('Cancel edit'))}</button></div>
      <p id="scheduleCheckResult" role="status"></p><p id="scheduleError" class="error-box" role="alert" tabindex="-1" hidden></p></form>`;
    view.append(panel);
    const runTab = $('#batchRunTab'), scheduleTab = $('#batchSchedulesTab');
    function selectTab(scheduled) {
      manual.hidden = scheduled; panel.hidden = !scheduled;
      runTab.setAttribute('aria-selected', String(!scheduled)); scheduleTab.setAttribute('aria-selected', String(scheduled));
      runTab.tabIndex = scheduled ? -1 : 0; scheduleTab.tabIndex = scheduled ? 0 : -1;
      if (scheduled) refresh();
    }
    runTab.onclick = () => selectTab(false); scheduleTab.onclick = () => selectTab(true);
    tabs.onkeydown = event => { if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); const next = event.target === runTab; selectTab(next); (next ? scheduleTab : runTab).focus(); } };
    const scheduleAct = async fn => { try { await fn(); } catch(error) { $('#scheduleError').textContent=t('Schedule action failed. Details: {details}',{details:error.message}); $('#scheduleError').hidden=false; $('#scheduleError').focus(); } };
    let editing = null, listSignature = '', loading = false, latest = null;
    const interval = $('#scheduleInterval');
    function intervalFields() { const daily=interval.value === 'daily'; $('#scheduleEveryLabel').hidden=daily; $('#scheduleEvery').disabled=daily; $('#scheduleDailyLabel').hidden=!daily; $('#scheduleDaily').disabled=!daily; }
    interval.onchange = intervalFields;
    function modelOptions(models) {
      const select = $('#scheduleModel'), old = select.value;
      const allowed = model => model.kind !== 'cascade' || (model.cascade_kind === 'escalate' && !model.cascade_has_fallback);
      const rows = models.filter(model => model.state === 'ready' && allowed(model)).map(model => ({id:model.id,disabled:false}));
      if (old && !rows.some(row => row.id === old)) {
        const existing=models.find(model => model.id === old);
        rows.push({id:old,disabled:Boolean(existing && !allowed(existing))});
      }
      if (JSON.stringify([...select.options].map(option => [option.value,option.disabled])) !== JSON.stringify(rows.map(row => [row.id,row.disabled]))) {
        select.innerHTML = rows.map(row => `<option value="${esc(row.id)}"${row.disabled?' disabled':''}>${esc(row.id)}${row.disabled?' · '+esc(t('Batch unavailable for this Cascade')):''}</option>`).join('');
        if (old && rows.some(row => row.id === old)) select.value = old;
      }
    }
    function resetForm() { editing = null; $('#scheduleForm').reset(); $('#scheduleFormTitle').textContent = t('Create schedule'); $('#scheduleCancel').hidden = true; $('#scheduleError').hidden = true; intervalFields(); }
    $('#scheduleCancel').onclick = resetForm;
    async function checkFolders() {
      const results = await Promise.all(['input_dir','output_dir'].map((kind, index) => api('/api/paths/check', {method:'POST',body:JSON.stringify({kind,path:$(index ? '#scheduleOutputDir' : '#scheduleInputDir').value})})));
      const good = results.every(result => result.ok);
      $('#scheduleCheckResult').textContent = good ? t('Folders are ready.') : t('Check the folder paths and permissions.');
      return good;
    }
    $('#scheduleCheck').onclick = () => scheduleAct(checkFolders);
    $('#scheduleForm').onsubmit = event => { event.preventDefault(); scheduleAct(async () => {
      try {
        if (!await checkFolders()) return;
        if ($('#scheduleModel').selectedOptions[0]?.disabled) throw new Error(t('Batch unavailable for this Cascade'));
        const n = Number($('#scheduleEvery').value), type = interval.value;
        if (type !== 'daily' && (!Number.isSafeInteger(n) || n < 1 || n * (type === 'hours' ? 60 : 1) > 525600)) throw new Error(t('Enter a valid interval.'));
        const body = {model:$('#scheduleModel').value,input_dir:$('#scheduleInputDir').value,output_dir:$('#scheduleOutputDir').value,pattern:$('#schedulePattern').value,concurrency:Number($('#scheduleConcurrency').value),interval:type === 'daily' ? {daily_at:$('#scheduleDaily').value} : {every_minutes:n * (type === 'hours' ? 60 : 1)},overwrite:$('#scheduleOverwrite').checked,catch_up:$('#scheduleCatchUp').checked};
        await api(editing ? `/api/schedules/${encodeURIComponent(editing)}` : '/api/schedules', {method:editing?'PUT':'POST',body:JSON.stringify(body)});
        resetForm(); await refresh(true);
      } catch (error) { $('#scheduleError').textContent = t('Unable to save or load schedules. Details: {details}',{details:error.message}); $('#scheduleError').hidden = false; $('#scheduleError').focus(); }
    }); };
    function runReason(row) {
      const raw=row.error||row.skipped||row.cancelled||row.reason||'Completed';
      if (!row.error) return t(raw);
      const line=String(raw).match(/^(.+): line (\d+):? /);
      if (line) return t('File {file}, line {line}: invalid request. Details: {details}',{file:line[1],line:line[2],details:raw});
      return t('Run failed. Details: {details}',{details:raw});
    }
    function draw(data) {
      if ($('#scheduleList').contains(document.activeElement)) return;
      const signature = JSON.stringify(data); if (signature === listSignature) return; listSignature = signature;
      $('#scheduleList').innerHTML = data.length ? data.map(({spec, runs}) => `<article class="model-row"><h3>${esc(spec.id)}</h3><p>${esc(spec.model)} · ${esc(spec.input_dir)} / ${esc(spec.pattern)} · ${esc(spec.output_dir)}</p><p>${esc(spec.paused?t('Paused'):t('Active'))}${spec.paused_reason ? ' · '+esc(t(spec.paused_reason)) : ''}</p><div class="controls"><button type="button" data-action="edit" data-id="${esc(spec.id)}">${esc(t('Edit'))}</button><button type="button" data-action="pause" data-id="${esc(spec.id)}">${esc(spec.paused?t('Resume'):t('Pause'))}</button><button type="button" data-action="preview" data-id="${esc(spec.id)}">${esc(t('Preview next request'))}</button><button type="button" data-action="run" data-id="${esc(spec.id)}">${esc(t('Run now'))}</button><button type="button" data-action="delete" data-id="${esc(spec.id)}">${esc(t('Delete'))}</button></div><div data-preview role="status"></div><h4>${esc(t('Recent runs'))}</h4>${runs.runs.length ? runs.runs.map(row=>`<p class="schedule-run">${esc(new Date(row.at*1000).toLocaleString())} · ${esc(row.file||'—')} · ${esc(row.model)} · ${row.duration == null ? '—' : esc(row.duration)+' s'} · ${esc(row.rows)} ${esc(t('rows'))} · ${esc(row.errors)} ${esc(t('errors'))} · ${esc(row.output||'—')} · ${esc(runReason(row))}</p>`).join('') : `<p class="muted">${esc(t('No runs yet.'))}</p>`}${runs.next_cursor!==null?`<button type="button" data-action="more" data-id="${esc(spec.id)}" data-cursor="${runs.next_cursor}">${esc(t('Load more'))}</button>`:''}</article>`).join('') : `<p class="muted">${esc(t('No schedules yet.'))}</p>`;
    }
    async function refresh(force = false) {
      if (loading || panel.hidden) return; loading = true;
      try {
        const {schedules} = await api('/api/schedules');
        const data = await Promise.all(schedules.map(async spec => ({spec, runs:await api(`/api/schedules/${encodeURIComponent(spec.id)}/runs`)})));
        for(const item of data){const previous=latest?.find(old=>old.spec.id===item.spec.id);if(previous && previous.runs.runs.length>100){const ids=new Set(item.runs.runs.map(row=>row.run_id));item.runs.runs.push(...previous.runs.runs.filter(row=>!ids.has(row.run_id)));item.runs.next_cursor=previous.runs.next_cursor===null?null:item.runs.runs.length;}}
        latest = data; if (force) listSignature = ''; draw(data);
      } catch (error) { $('#scheduleError').textContent = t('Unable to save or load schedules. Details: {details}',{details:error.message}); $('#scheduleError').hidden = false; }
      finally { loading = false; }
    }
    $('#scheduleList').addEventListener('focusout', () => setTimeout(() => { if (latest) draw(latest); }, 0));
    $('#scheduleList').onclick = event => { const button=event.target.closest('button[data-action]'); if (!button) return; scheduleAct(async () => {
      const spec = latest?.find(item => item.spec.id === button.dataset.id)?.spec; if (!spec) return;
      const url = `/api/schedules/${encodeURIComponent(spec.id)}`;
      if (button.dataset.action === 'edit') {
        editing=spec.id; $('#scheduleFormTitle').textContent=t('Edit schedule'); $('#scheduleCancel').hidden=false;
        const select=$('#scheduleModel'); if (![...select.options].some(option=>option.value===spec.model)) {const model=(statusCache?.models||[]).find(row=>row.id===spec.model), unavailable=model?.kind==='cascade'&&(model.cascade_kind!=='escalate'||model.cascade_has_fallback), option=new Option(spec.model+(unavailable?' · '+t('Batch unavailable for this Cascade'):''),spec.model);option.disabled=Boolean(unavailable);select.add(option);} select.value=spec.model;
        $('#scheduleInputDir').value=spec.input_dir; $('#scheduleOutputDir').value=spec.output_dir; $('#schedulePattern').value=spec.pattern; $('#scheduleConcurrency').value=spec.concurrency;
        interval.value=spec.interval.daily_at?'daily':'minutes'; $('#scheduleEvery').value=spec.interval.every_minutes||60; $('#scheduleDaily').value=spec.interval.daily_at||'09:00'; $('#scheduleOverwrite').checked=spec.overwrite; $('#scheduleCatchUp').checked=spec.catch_up; intervalFields(); $('#scheduleInputDir').focus(); return;
      }
      if (button.dataset.action === 'preview') {const preview=await api(url+'/preview');const node=button.closest('article').querySelector('[data-preview]');node.textContent=preview.file?(preview.inspection.ok?t('Preview of {file}: {preview}',{file:preview.file,preview:preview.inspection.first?.preview||''}):t('Unable to preview {file}. Details: {details}',{file:preview.file,details:preview.inspection.schema_error||preview.inspection.code})):t('No new files');return;}
      if (button.dataset.action === 'delete') { if (!await confirmDialog({title:t('Delete schedule?'),description:t('The schedule and its settings will be removed. Existing input and output files remain.'),confirmLabel:t('Delete'),destructive:true})) return; await api(url,{method:'DELETE'}); }
      if (button.dataset.action === 'pause') await api(url,{method:'PUT',body:JSON.stringify({paused:!spec.paused,paused_reason:null})});
      if (button.dataset.action === 'run') {const result=await api(url+'/run',{method:'POST'}); toast(t({started:'Batch started',queued:'Batch queued',skipped:'Batch skipped',failed:'Batch failed',no_new_files:'No new files'}[result.status] || 'No new files'));}
      if (button.dataset.action === 'more') { const result=await api(url+'/runs?cursor='+encodeURIComponent(button.dataset.cursor)); const item=latest.find(row=>row.spec.id===spec.id); item.runs.runs.push(...result.runs); item.runs.next_cursor=result.next_cursor; listSignature=''; $('#schedulePanelTitle').focus(); draw(latest); $('#scheduleList').querySelector('[data-action=more]')?.focus(); return; }
      $('#schedulePanelTitle').focus(); await refresh(true); const restored=[...$('#scheduleList').querySelectorAll('button[data-action]')].find(node=>node.dataset.id===spec.id && node.dataset.action===button.dataset.action); if(restored)restored.focus();
    }); };
    document.addEventListener('app:status', event => { modelOptions(event.detail.models||[]); if (!panel.hidden) refresh(); });
    modelOptions(statusCache?.models||[]); intervalFields();
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, {once:true}); else boot();
})();
