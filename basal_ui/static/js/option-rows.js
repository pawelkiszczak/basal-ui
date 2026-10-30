// Option rows: a question's options, labels, claims, levels or yes / no descriptions as one row each (key and
// description) over the card's hidden textarea (data-f), which stays the builder's source: the rows write its
// "key: description" lines, so parsing, the unedited-field check, the language draft and Edit JSON keep working.
'use strict';
let optionFieldId=0;
// mode: keyed (key + description), levels (ordered, no keys), yesno (two fixed rows: true, false)
// label, help and add arrive translated (t() at the call site, where the i18n test sees the literals)
function optionField(f, text, label, help, add=t('Add option')) {
  const id=`option-field-${++optionFieldId}`;
  return `<div class="qf option-field" data-option-for="${f}"><span class="qf-label" id="${id}-label">${label}</span><div class="option-rows" role="list" aria-labelledby="${id}-label" aria-describedby="${id}-help"></div><div class="option-actions"><button type="button" class="add-option" data-variant="outline">${add}</button><small class="muted option-keys-help">${t('Enter adds a row · Alt + ↑ / ↓ moves it')}</small></div><textarea class="option-source" data-f="${f}" hidden aria-hidden="true" tabindex="-1">${esc(text??'')}</textarea><small class="muted" id="${id}-help">${help}</small><div class="option-hint reason warning" role="status" hidden></div></div>`;
}
function optionMode(card, field) {
  const f=field.dataset.optionFor, type=$('.q-type',card).value;
  if(f==='criteria'&&(type==='noul'||(type==='act'&&$('[data-f="yesno"]',card)?.checked)))return 'yesno';
  return f==='criteria'&&type==='score'?'levels':'keyed';
}
function rowsFromText(text, mode) {
  if(mode==='yesno'){const lines=splitLines(text);return [{key:'true',desc:lines[0]||''},{key:'false',desc:lines[1]||''}];}
  const lines=splitLines(text), keyed=mode==='keyed'&&lines.length>0&&lines.every(x=>KEYED.test(x));
  return lines.map(line=>{const m=keyed&&line.match(KEYED);return m?{key:m[1],desc:m[2]}:{key:'',desc:line};});
}
function textFromRows(rows, mode) {
  if(mode==='yesno')return rows.map(r=>r.desc.trim()).join('\n').replace(/\n$/,'');
  return rows.map(r=>{const k=r.key.trim(), d=r.desc.trim();return k&&d?`${k}: ${d}`:k||d;}).filter(Boolean).join('\n');
}
// why the rows will not go out as explicit keys (the builder then sends every row as text under hidden keys 0…n)
function optionHint(rows, mode) {
  if(mode==='yesno')return !rows[0].desc.trim()&&rows[1].desc.trim()?t('Describe yes before no: a single line describes yes.'):'';
  if(mode!=='keyed')return '';
  const used=rows.filter(r=>r.key.trim()||r.desc.trim()), keyed=used.filter(r=>r.key.trim());
  if(keyed.some(r=>!/^[^\s:]{1,40}$/.test(r.key.trim())))return t('Keys cannot contain spaces or colons and have at most 40 characters: these rows are sent as plain text with hidden keys.');
  if(keyed.length&&(keyed.length<used.length||keyed.some(r=>!r.desc.trim())))return t('Some rows have no key or no description: every row is sent as plain text with hidden keys 0…n. Fill both in every row, or clear the keys.');
  return '';
}
function renderOptionRows(card, field, focus=null) {
  if(!field){card.querySelectorAll('.option-field').forEach(x=>renderOptionRows(card,x));return;}
  const source=$('.option-source',field), mode=optionMode(card,field), locked=source.disabled;
  if(!field._rows||field._mode!==mode||field._text!==source.value)field._rows=rowsFromText(source.value,mode);
  field._mode=mode; field._text=source.value;
  const rows=field._rows, fixed=mode==='yesno'||locked, n=rows.length;
  $('.option-rows',field).innerHTML=rows.map((r,i)=>{
    const tag=mode==='yesno'?t(r.key==='true'?'Yes (true)':'No (false)'):mode==='levels'?String(i):String(i+1);
    const keyInput=mode==='keyed'?`<input class="input opt-in opt-key" type="text" value="${esc(r.key)}" placeholder="${t('key')}" aria-label="${t('Key {number}',{number:i+1})}"${locked?' disabled':''}>`:'';
    const what=mode==='yesno'?tag:mode==='levels'?t('Level {number}',{number:i}):t('Description {number}',{number:i+1});
    const buttons=fixed?'':`<button type="button" class="opt-move" data-move="-1" aria-label="${t('Move row {number} up',{number:i+1})}"${i===0?' disabled':''}>↑</button><button type="button" class="opt-move" data-move="1" aria-label="${t('Move row {number} down',{number:i+1})}"${i===n-1?' disabled':''}>↓</button><button type="button" class="opt-remove" aria-label="${t('Remove row {number}',{number:i+1})}">×</button>`;
    return `<div class="option-row${mode==='keyed'?' keyed':''}" role="listitem" data-row="${i}"><span class="option-tag" aria-hidden="true">${esc(tag)}</span>${keyInput}<input class="input opt-in opt-desc" type="text" value="${esc(r.desc)}" placeholder="${mode==='yesno'?t('optional'):t('description')}" aria-label="${esc(what)}"${locked?' disabled':''}>${buttons}</div>`;
  }).join('')||`<div class="muted option-empty" role="listitem">${t('No rows yet.')}</div>`;
  $('.add-option',field).hidden=$('.option-keys-help',field).hidden=fixed;
  const hint=optionHint(rows,mode), box=$('.option-hint',field); box.textContent=hint; box.hidden=!hint;
  field.querySelectorAll('.opt-key').forEach(x=>x.setAttribute('aria-invalid',String(Boolean(x.value.trim())&&!/^[^\s:]{1,40}$/.test(x.value.trim()))));
  if(focus){  // a move button that is now disabled (the row reached an end) hands the focus to the other one
    const row=field.querySelector(`[data-row="${focus.row}"]`), el=row&&$(focus.sel,row);
    (el&&!el.disabled?el:row?.querySelector('.opt-move:not([disabled])')||row?.querySelector('.opt-desc'))?.focus();
  }
}
function readOptionRows(field) {
  field._rows=[...field.querySelectorAll('.option-row')].map((row,i)=>({key:$('.opt-key',row)?.value??field._rows[i]?.key??'',desc:$('.opt-desc',row).value}));
}
// the rows went into the textarea: the card hears it as an edit of the field (cost matrix, request JSON)
function writeOptionRows(card, field) {
  const source=$('.option-source',field);
  source.value=textFromRows(field._rows,field._mode); field._text=source.value;
  source.dispatchEvent(new Event('input',{bubbles:true}));
}
function initOptionRows(card) {
  card.querySelectorAll('.option-field').forEach(field=>{
    renderOptionRows(card,field);
    if(field._wired)return; field._wired=true;
    const update=(focus=null)=>{renderOptionRows(card,field,focus);writeOptionRows(card,field);};
    const rowOf=el=>Number(el.closest('.option-row')?.dataset.row);
    const move=(i,by,sel)=>{
      const j=i+by, rows=field._rows; if(j<0||j>=rows.length)return;
      [rows[i],rows[j]]=[rows[j],rows[i]]; update({row:j,sel}); announce(t('Moved to position {number}',{number:j+1}));
    };
    const remove=i=>{
      field._rows.splice(i,1); const next=Math.min(i,field._rows.length-1);
      update(next<0?null:{row:next,sel:'.opt-desc'}); if(next<0)$('.add-option',field).focus(); announce(t('Row removed'));
    };
    field.addEventListener('input',e=>{
      if(!e.target.matches('.opt-in'))return;
      e.stopPropagation(); readOptionRows(field);
      const hint=optionHint(field._rows,field._mode), box=$('.option-hint',field); box.textContent=hint; box.hidden=!hint;
      if(e.target.matches('.opt-key'))e.target.setAttribute('aria-invalid',String(Boolean(e.target.value.trim())&&!/^[^\s:]{1,40}$/.test(e.target.value.trim())));
      writeOptionRows(card,field);
    });
    field.addEventListener('change',e=>{if(e.target.matches('.opt-in'))e.stopPropagation();});
    field.addEventListener('keydown',e=>{
      if(!e.target.matches('.opt-in')||field._mode==='yesno'||e.target.disabled)return;
      const i=rowOf(e.target), sel=e.target.matches('.opt-key')?'.opt-key':'.opt-desc';
      if(e.key==='Enter'&&!e.ctrlKey&&!e.metaKey&&!e.altKey){
        e.preventDefault(); readOptionRows(field); field._rows.splice(i+1,0,{key:'',desc:''});
        update({row:i+1,sel:field._mode==='keyed'?'.opt-key':'.opt-desc'});
      } else if(e.altKey&&(e.key==='ArrowUp'||e.key==='ArrowDown')){e.preventDefault();readOptionRows(field);move(i,e.key==='ArrowUp'?-1:1,sel);}
      else if(e.key==='Backspace'&&field._rows.length>1&&!field._rows[i].key&&!field._rows[i].desc&&e.target.value===''){
        e.preventDefault(); field._rows.splice(i,1); const prev=Math.max(0,i-1);
        update({row:prev,sel:'.opt-desc'}); announce(t('Row removed'));
      }
    });
    field.addEventListener('click',e=>{
      const b=e.target.closest('button'); if(!b)return;
      e.stopPropagation(); readOptionRows(field);
      if(b.matches('.add-option')){field._rows.push({key:'',desc:''});update({row:field._rows.length-1,sel:field._mode==='keyed'?'.opt-key':'.opt-desc'});}
      else if(b.matches('.opt-move'))move(rowOf(b),Number(b.dataset.move),b.dataset.move==='-1'?'[data-move="-1"]':'[data-move="1"]');
      else if(b.matches('.opt-remove'))remove(rowOf(b));
    });
  });
}
