// Option rows: a question's options, labels, claims, levels or yes / no descriptions as one row each, {key, desc}.
// The rows are the source of the request: a key is a key only when it is typed in a key box, never read out of a
// description ("Refund: full amount" in a keyless row stays that text). The card's hidden textarea (data-f) holds the
// rows as JSON, so the unedited-field check, the card snapshot and the language draft see every edit losslessly.
'use strict';
let optionFieldId=0;
const OPTION_KEY=/^[^\s:]{1,40}$/;  // an explicit option key: no spaces or colons, at most 40 characters
// the rows as the textarea's text and back; a text that is not the rows' JSON is one keyless row per line
const encodeOptionRows=rows=>JSON.stringify(rows.map(r=>[r.key,r.desc]));
function decodeOptionRows(text) {
  try{const v=JSON.parse(text);if(Array.isArray(v))return v.map(r=>({key:String(r?.[0]??''),desc:String(r?.[1]??'')}));}catch{}
  return String(text||'').split('\n').map(x=>x.trim()).filter(Boolean).map(desc=>({key:'',desc}));
}
const rowText=v=>typeof v==='string'?v:JSON.stringify(v);
const labelObjectList=v=>Array.isArray(v)&&v.length>0&&v.every(x=>x&&typeof x==='object'&&!Array.isArray(x)&&'name' in x);
// a request value as rows. mode: keyed (key + description), levels (ordered, no keys), yesno (true, false).
// hideKeys: criteria sent with option_keys "hide" (their keys are 0…n, not the user's): the rows show no keys
function optionRowsOf(value, mode, hideKeys=false) {
  if(mode==='yesno'){
    const c=value&&typeof value==='object'&&!Array.isArray(value)?value:{};
    return [{key:'true',desc:c.true==null?'':rowText(c.true)},{key:'false',desc:c.false==null?'':rowText(c.false)}];
  }
  if(labelObjectList(value))return value.map(x=>{const d=x.rubric??x.description;return {key:mode==='keyed'?String(x.name):'',desc:d==null?(mode==='keyed'?'':String(x.name)):rowText(d)};});
  if(Array.isArray(value))return value.map(x=>({key:'',desc:rowText(x)}));
  if(value&&typeof value==='object')return Object.entries(value).map(([k,v])=>
    mode==='levels'?{key:'',desc:hideKeys?rowText(v):v==null?k:`${k}: ${rowText(v)}`}
    :hideKeys?{key:'',desc:rowText(v)}:v==null?{key:'',desc:k}:{key:k,desc:rowText(v)});
  return [];
}
// rows carried over when the mode changes (act's "Yes / no outcomes" switch)
function optionRowsForMode(rows, from, to) {
  if(to==='yesno'){
    const byKey=k=>rows.find(r=>r.key.trim()===k), descs=rows.map(r=>r.desc.trim()).filter(Boolean);
    const named=byKey('true')||byKey('false');
    return [{key:'true',desc:named?byKey('true')?.desc??'':descs[0]??''},{key:'false',desc:named?byKey('false')?.desc??'':descs[1]??''}];
  }
  if(from==='yesno')return rows.filter(r=>r.desc.trim()).map(r=>({key:'',desc:r.desc}));
  return rows;
}
// the rows a request gets, and what is wrong with them. kind: hidden (choice and act options: keyless rows go out
// under hidden keys 0…n), list (multi, classify and verify: keyless rows go out as a list), labels (classify labels
// given as [{name, rubric}]: each row its own label, the key its name)
function optionKeyProblems(rows, mode, kind) {
  if(mode!=='keyed')return {invalid:new Set(),duplicate:new Set()};
  const keys=rows.map(r=>r.key.trim()).filter(Boolean), seen=new Set(), duplicate=new Set();
  keys.forEach(k=>{if(seen.has(k))duplicate.add(k);seen.add(k);});
  return {invalid:new Set(kind==='labels'?[]:keys.filter(k=>!OPTION_KEY.test(k))),duplicate};
}
function optionRowsValue(rows, mode, kind='list', rubricKey='rubric') {
  const used=rows.map(r=>({key:r.key.trim(),desc:r.desc.trim()})).filter(r=>r.key||r.desc);
  const {invalid,duplicate}=optionKeyProblems(rows,mode,kind);
  const error=duplicate.size?t('Duplicate key {key}: every key must be unique.',{key:[...duplicate].join(', ')}):null;
  if(mode==='yesno'){
    const c={}; if(rows[0]?.desc.trim())c.true=rows[0].desc.trim(); if(rows[1]?.desc.trim())c.false=rows[1].desc.trim();
    return {value:Object.keys(c).length?c:null,error:null};
  }
  if(mode==='levels')return {value:used.map(r=>r.desc||r.key),error:null};
  if(kind==='labels')return {value:used.map(r=>r.key?{name:r.key,...(r.desc?{[rubricKey]:r.desc}:{})}:{name:r.desc}),error};
  const keyed=used.length>0&&!invalid.size&&used.every(r=>r.key&&r.desc), text=r=>r.key&&r.desc?`${r.key}: ${r.desc}`:r.key||r.desc;
  if(keyed)return {value:Object.fromEntries(used.map(r=>[r.key,r.desc])),keyed:true,error};
  return {value:kind==='hidden'?Object.fromEntries(used.map((r,i)=>[String(i),text(r)])):used.map(text),keyed:false,error};
}
// label, help and add arrive translated (t() at the call site, where the i18n test sees the literals)
function optionField(f, rows, label, help, add=t('Add option'), kind='list') {
  const id=`option-field-${++optionFieldId}`;
  return `<div class="qf option-field" data-option-for="${f}" data-option-kind="${kind}"><span class="qf-label" id="${id}-label">${label}</span><div class="option-rows" role="list" aria-labelledby="${id}-label" aria-describedby="${id}-help"></div><div class="option-actions"><button type="button" class="add-option" data-variant="outline">${add}</button><small class="muted option-keys-help">${t('Enter adds a row · Alt + ↑ / ↓ moves it')}</small></div><textarea class="option-source" data-f="${f}" hidden aria-hidden="true" tabindex="-1">${esc(encodeOptionRows(rows))}</textarea><small class="muted" id="${id}-help">${help}</small><div class="option-hint reason warning" id="${id}-hint" role="status" hidden></div></div>`;
}
function optionMode(card, field) {
  const f=field.dataset.optionFor, type=$('.q-type',card).value;
  if(f==='criteria'&&(type==='noul'||(type==='act'&&$('[data-f="yesno"]',card)?.checked)))return 'yesno';
  return f==='criteria'&&type==='score'?'levels':'keyed';
}
// the rows of a card's option field as they will be sent (field: data-f name)
function cardOptionRows(card, f) {
  const field=card.querySelector(`.option-field[data-option-for="${f}"]`); if(!field)return null;
  const mode=optionMode(card,field), source=$('.option-source',field);
  if(!field._rows||field._text!==source.value)return {rows:optionRowsForMode(decodeOptionRows(source.value),field._mode||mode,mode),mode,kind:field.dataset.optionKind};
  return {rows:field._mode===mode?field._rows:optionRowsForMode(field._rows,field._mode,mode),mode,kind:field.dataset.optionKind};
}
// why the rows will not go out as the user typed them (a duplicate key stops the request: parseQuestionCard)
function optionHint(rows, mode, kind) {
  if(mode!=='keyed')return '';
  const {invalid,duplicate}=optionKeyProblems(rows,mode,kind);
  if(duplicate.size)return t('Duplicate key {key}: every key must be unique.',{key:[...duplicate].join(', ')});
  if(kind==='labels')return '';
  const used=rows.filter(r=>r.key.trim()||r.desc.trim()), keyed=used.filter(r=>r.key.trim());
  if(invalid.size)return t('Keys cannot contain spaces or colons and have at most 40 characters: these rows are sent as plain text with hidden keys.');
  if(keyed.length&&(keyed.length<used.length||keyed.some(r=>!r.desc.trim())))return t('Some rows have no key or no description: every row is sent as plain text with hidden keys 0…n. Fill both in every row, or clear the keys.');
  return '';
}
// the hint under the rows and the invalid key boxes (described by the hint)
function showOptionProblems(field) {
  const rows=field._rows, mode=field._mode, kind=field.dataset.optionKind, box=$('.option-hint',field);
  const hint=optionHint(rows,mode,kind), {invalid,duplicate}=optionKeyProblems(rows,mode,kind);
  box.textContent=hint; box.hidden=!hint;
  box.classList.toggle('warning',!duplicate.size);  // a duplicate key is an error (it stops the request)
  field.querySelectorAll('.option-row').forEach((row,i)=>{
    const input=$('.opt-key',row); if(!input)return;
    const key=rows[i]?.key.trim()||'', bad=Boolean(key)&&(invalid.has(key)||duplicate.has(key));
    input.setAttribute('aria-invalid',String(bad));
    if(bad)input.setAttribute('aria-describedby',box.id); else input.removeAttribute('aria-describedby');
  });
}
function renderOptionRows(card, field, focus=null) {
  if(!field){card.querySelectorAll('.option-field').forEach(x=>renderOptionRows(card,x));return;}
  const source=$('.option-source',field), locked=source.disabled, {rows:current,mode}=cardOptionRows(card,field.dataset.optionFor);
  if(field._mode&&field._mode!==mode)source.value=encodeOptionRows(current);  // the carried-over rows, for the draft
  field._rows=current; field._mode=mode; field._text=source.value;
  const rows=field._rows, fixed=mode==='yesno'||locked, n=rows.length;
  $('.option-rows',field).innerHTML=rows.map((r,i)=>{
    const tag=mode==='yesno'?(r.key==='true'?t('Yes (true)'):t('No (false)')):mode==='levels'?String(i):String(i+1);
    const keyInput=mode==='keyed'?`<input class="input opt-in opt-key" type="text" value="${esc(r.key)}" placeholder="${t('key')}" aria-label="${t('Key {number}',{number:i+1})}"${locked?' disabled':''}>`:'';
    const what=mode==='yesno'?tag:mode==='levels'?t('Level {number}',{number:i}):t('Description {number}',{number:i+1});
    const buttons=fixed?'':`<button type="button" class="opt-move" data-move="-1" aria-label="${t('Move row {number} up',{number:i+1})}"${i===0?' disabled':''}>↑</button><button type="button" class="opt-move" data-move="1" aria-label="${t('Move row {number} down',{number:i+1})}"${i===n-1?' disabled':''}>↓</button><button type="button" class="opt-remove" aria-label="${t('Remove row {number}',{number:i+1})}">×</button>`;
    return `<div class="option-row${mode==='keyed'?' keyed':''}" role="listitem" data-row="${i}"><span class="option-tag" aria-hidden="true">${esc(tag)}</span>${keyInput}<input class="input opt-in opt-desc" type="text" value="${esc(r.desc)}" placeholder="${mode==='yesno'?t('optional'):t('description')}" aria-label="${esc(what)}"${locked?' disabled':''}>${buttons}</div>`;
  }).join('')||`<div class="muted option-empty" role="listitem">${t('No rows yet.')}</div>`;
  $('.add-option',field).hidden=$('.option-keys-help',field).hidden=fixed;
  showOptionProblems(field);
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
  source.value=encodeOptionRows(field._rows); field._text=source.value;
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
      e.stopPropagation(); readOptionRows(field); showOptionProblems(field); writeOptionRows(card,field);
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
