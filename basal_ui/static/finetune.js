'use strict';

// Fine-tune shares the app's catalog and status events; it does not own a polling timer.
(() => {
  const block=$('#finetuneBlock');
  block.innerHTML=`<div class="section-head"><h3>${t('Fine-tune')}</h3></div>
    <p>${t('Adapt a downloaded bf16 basal or Attached model using labelled Decisions. The base weights are not changed.')}</p>
    <p class="reason">${t('Upstream tested fine-tuning on Apple Silicon with basal-omni-pico; other GPUs are untested.')}</p>
    <p class="reason">${t('Fine-tuning is unavailable on native Windows and systems without secure private output operations.')}</p>
    <p>${t('Fine-tuning writes a full merged model and an adapter. It reserves twice the estimated model memory and can use substantial disk space.')}</p>
    <p id="finetuneAvailability" class="reason" role="status"></p>
    <p><a href="#backends">${t('Open Backends to install Engine add-ons')}</a></p>
    <div class="manager-fields">
      <label class="manager-field">${t('Model to fine-tune')}<select id="finetuneModel"></select></label>
      <label class="manager-field">${t('Labelled requests')}<input id="finetuneInput" type="text" placeholder="${t('/path/to/labelled.jsonl')}"></label>
      <label class="manager-field">${t('Output name')}<input id="finetuneName" type="text" maxlength="64" autocomplete="off"></label>
      <label class="manager-field">${t('Epochs')}<input id="finetuneEpochs" type="number" min="0.000001" step="any" value="2"></label>
      <label class="manager-field">${t('Learning rate')}<input id="finetuneLr" type="number" min="0.000000001" step="any" value="0.0001"></label>
      <label class="manager-field">${t('LoRA rank')}<input id="finetuneRank" type="number" min="1" step="1" value="16"></label>
      <label class="manager-field">${t('LoRA alpha')}<input id="finetuneAlpha" type="number" min="1" step="1" value="32"></label>
      <label class="manager-field">${t('Held-out share')}<input id="finetuneHoldout" type="number" min="0.001" max="0.999" step="any" value="0.25"></label>
      <label class="manager-field">${t('Maximum sequence length')}<input id="finetuneMaxLen" type="number" min="1" step="1" value="2048"></label>
      <label class="manager-field">${t('Maximum optimizer steps (optional)')}<input id="finetuneMaxSteps" type="number" min="1" step="1"></label>
      <label class="finetune-check"><input id="finetuneCheckpointing" type="checkbox"> ${t('Gradient checkpointing')}</label>
    </div>
    <div class="manager-actions"><button type="button" id="finetuneStart" disabled>${t('Start fine-tuning')}</button><button type="button" id="finetuneCancel" hidden>${t('Cancel')}</button><a href="#logs/job%3Afinetune">${t('Logs')}</a></div>
    <p id="finetuneProgress" role="status" aria-live="polite"></p><p id="finetuneError" class="reason" role="alert"></p><p id="finetuneCleanup" class="reason" role="alert"></p><p id="finetuneValidation" class="muted"></p>
    <h4>${t('Fine-tune results')}</h4><div id="finetuneResults"></div>`;
  const select=$('#finetuneModel'), results=$('#finetuneResults'), progress=$('#finetuneProgress');
  let known=[], available=false, loading=false, seq=0, signature='', jobSignature='', pending=false;
  const eligible=m=>m?.downloaded&&m.port==='bf16'&&(m.family==='attached'||m.family?.startsWith('basal-'));
  function renderModels() {
    const current=select.value||localStorage.getItem('basal-ui.finetuneModel')||'';
    const rows=modelData.filter(eligible), ids=[...new Set([...rows.map(m=>m.id),...known])];
    const next=JSON.stringify([ids,rows.map(m=>m.id)]);
    if(signature===next)return;
    signature=next;
    select.innerHTML=ids.map(id=>`<option value="${esc(id)}">${esc(id)}${rows.some(m=>m.id===id)?'':` (${t('unavailable for training')})`}</option>`).join('')||`<option value="">${t('No models or results')}</option>`;
    if(ids.includes(current))select.value=current;
    load();
  }
  function controls() {
    const selected=modelData.find(m=>m.id===select.value), job=statusCache?.jobs?.finetune;
    $('#finetuneStart').disabled=!available||loading||pending||!eligible(selected)||Boolean(job?.running);
    $('#finetuneCancel').hidden=!(job?.running&&job.model===select.value);
  }
  function status(job) {
    controls();
    if(!job){progress.textContent='';return;}
    if(job.model!==select.value){progress.textContent=job.running?t('Fine-tuning is running for another model.'):'';return;}
    progress.textContent=job.running?`${t('Fine-tuning')} · ${ts(job.phase,job.phase_code,job.phase_params)}…`:job.phase==='error'?t('Failed: {error}',{error:job.error}):job.phase==='cancelled'?t('Cancelled'):job.phase==='done'?t('Fine-tuning complete; review the held-out report.') : '';
  }
  function renderResults(data) {
    if(!data.length){results.innerHTML=`<p class="muted">${t('No fine-tune results for this model.')}</p>`;return;}
    results.innerHTML=data.map(row=>{
      const score=row.scored==null||row.scored===0?t('Not scored'):t('{before} before · {after} after',{before:row.top1_before==null?'—':pct(row.top1_before),after:row.top1_after==null?'—':pct(row.top1_after)});
      return `<article class="finetune-result"><h5>${esc(row.name)}</h5><p class="finetune-path">${t('Output')}: <code>${esc(row.output)}</code></p>${row.adapter?`<p class="finetune-path">${t('Adapter')}: <code>${esc(row.adapter)}</code></p>`:''}${row.cleanup_pending?`<p class="reason">${t('Cleanup is incomplete. Retry deletion.')}</p><p class="finetune-path"><code>${esc(row.retained_path)}</code></p>`:row.ready?`<p>${t('Held-out accuracy')}: ${esc(score)}</p><p>${t('Training decisions')}: ${esc(row.train_decisions??'—')} · ${t('Held-out lines')}: ${esc(row.heldout_lines??'—')} · ${t('Scored decisions')}: ${esc(row.scored??'—')}</p><p>${t('Skipped')}: <code>${esc(JSON.stringify(row.skipped??{}))}</code> · ${t('Steps')}: ${esc(row.steps??'—')} · ${t('Seconds')}: ${esc(row.seconds??'—')}</p><p class="muted">${t('Accuracy is not a certificate. Calibrate the adapted model on held-out data.')}</p>`:`<p class="reason">${t('Output unavailable or incomplete')}: ${esc(row.error||'')}</p>`}<button type="button" data-finetune-delete="${esc(row.name)}">${t('Delete output')}</button></article>`;
    }).join('');
  }
  async function load() {
    const id=select.value, ticket=++seq;
    results.innerHTML='';loading=true;controls();
    try{
      const data=await api(`/api/finetune${id?`?id=${encodeURIComponent(id)}`:''}`);
      if(ticket!==seq||id!==select.value)return;
      available=data.available===true;known=data.models||[];
      $('#finetuneAvailability').textContent=ts(data.reason,data.reason_code,data.reason_params)||(!eligible(modelData.find(m=>m.id===id))&&id?t('This model is unavailable for training; its saved results can still be removed.'): '');
      renderResults(data.results||[]);renderModels();
    }catch(error){if(ticket===seq&&id===select.value){available=false;$('#finetuneAvailability').textContent=error.message;}}
    finally{if(ticket===seq&&id===select.value){loading=false;controls();status(statusCache?.jobs?.finetune);}}
  }
  select.onchange=()=>{localStorage.setItem('basal-ui.finetuneModel',select.value);$('#finetuneValidation').textContent='';$('#finetuneCleanup').textContent='';$('#finetuneError').textContent='';progress.textContent='';load();};
  $('#finetuneStart').onclick=async()=>{
    const id=select.value, ticket=seq;if(!id)return;
    $('#finetuneError').textContent='';
    const data={id,input:$('#finetuneInput').value.trim(),out_name:$('#finetuneName').value.trim(),epochs:Number($('#finetuneEpochs').value),lr:Number($('#finetuneLr').value),rank:Number($('#finetuneRank').value),alpha:Number($('#finetuneAlpha').value),holdout:Number($('#finetuneHoldout').value),max_len:Number($('#finetuneMaxLen').value),grad_checkpointing:$('#finetuneCheckpointing').checked};
    if($('#finetuneMaxSteps').value!=='')data.max_steps=Number($('#finetuneMaxSteps').value);
    pending=true;controls();
    try{const job=await api('/api/finetune',{method:'POST',body:JSON.stringify(data)});if(ticket===seq&&id===select.value){const v=job.validation||{};$('#finetuneValidation').textContent=t('Preflight: {train} training candidates, {held} held-out lines, skipped: {skipped}; task decisions skipped: {tasks}.',{train:v.training_candidates??'—',held:v.heldout_lines??'—',skipped:JSON.stringify(v.skipped_training??{}),tasks:v.skipped_task_decisions??'—'});}await refreshStatus();}
    catch(error){if(ticket===seq&&id===select.value)$('#finetuneError').textContent=error.message;}
    finally{pending=false;controls();}
  };
  $('#finetuneCancel').onclick=()=>act(async()=>{await api('/api/finetune',{method:'DELETE'});await refreshStatus();});
  results.onclick=async event=>{
    const button=event.target.closest('[data-finetune-delete]');if(!button)return;
    const id=select.value,name=button.dataset.finetuneDelete,ticket=seq;
    $('#finetuneError').textContent='';
    const yes=await confirmDialog({title:t('Delete fine-tune output?'),description:t('Delete {name} and its full merged model, adapter and held-out labels? Saved load options may still point to its adapter.',{name}),confirmLabel:t('Delete output'),destructive:true});
    if(!yes||ticket!==seq||id!==select.value)return;
    button.disabled=true;
    try{const result=await api('/api/finetune/delete',{method:'POST',body:JSON.stringify({id,name})});if(result.retained_path){const message=t('Cleanup was incomplete. Inspect the retained directory: {path}',{path:result.retained_path});toast(message,'error');if(ticket===seq&&id===select.value)$('#finetuneCleanup').textContent=message;}else toast(t('Removed {bytes} bytes of files.',{bytes:result.removed_bytes??0}));if(ticket===seq&&id===select.value)await load();}
    catch(error){if(ticket===seq&&id===select.value)$('#finetuneError').textContent=error.message;}
    finally{button.disabled=false;}
  };
  document.addEventListener('app:models',()=>renderModels());
  document.addEventListener('app:status',event=>{
    const job=event.detail?.jobs?.finetune, addon=event.detail?.jobs?.['engine-addon'];
    const next=JSON.stringify([job?.started,job?.running,job?.phase,addon?.started,addon?.running,addon?.phase]);
    status(job);
    if(next!==jobSignature){jobSignature=next;if(!job?.running||!addon?.running)load();}
  });
  renderModels();load();
})();
