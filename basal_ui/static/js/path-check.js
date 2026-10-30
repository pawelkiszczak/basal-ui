// Live path checks under the path fields (POST /api/paths/check): whether the file exists or can be written, its
// lines and the shape of its first row. Browsers do not reveal a picked file's path, so these fields name files on the
// machine basal-ui runs on.
'use strict';
// every problem code basal_ui/paths.py returns, translated here (literal t() calls: the i18n test sees each key, and
// tests/test_ui_frontend_contract.py checks this map against paths.py)
const PATH_PROBLEMS={
  missing:t('No file at this path.'),
  is_dir:t('This is a directory, not a file.'),
  not_file:t('This is not a regular file.'),
  unreadable:t('basal-ui cannot read this file.'),
  empty:t('The file is empty.'),
  not_json:t('The first line is not a JSON object.'),
  line_too_long:t('The first line is too long to check.'),
  unknown_format:t('The first row is neither a simple item nor a request with questions.'),
  not_text:t('The file is not UTF-8 text.'),
  too_large:t('The file is too large to check.'),
  dir_missing:t('The directory does not exist.'),
  not_writable:t('basal-ui cannot write here.'),
  invalid:t('This path is not valid here (too long, or a link loop).'),
  not_dir:t('This is a file, not a directory.'),
};
function pathSummary(r) {
  if(r.kind==='output')return r.exists?t('Exists: it will be replaced.'):t('A new file will be written.');
  if(r.kind==='append')return r.exists?t('Exists: new decisions are appended.'):t('A new file will be written.');
  if(r.kind==='dir')return r.exists?t('Directory exists.'):t('A new directory will be created.');
  if(r.kind==='json')return t('JSON file, {size}',{size:fileSize(r.size||0)});
  const f=r.first||{};
  return `${tp(r.lines,'{n} line','{n} lines')}${r.lines_capped?'+':''} · ${f.format==='simple'?t('simple items'):tp(f.questions,'{n} question per request','{n} questions per request')}${f.types?.length?` · ${f.types.join(', ')}`:''}${f.labelled?` · ${t('labelled (gold)')}`:''}`;
}
function attachPathCheck(input, kind) {
  if(!input)return;
  const hint=document.createElement('small'); hint.className='path-hint'; hint.id=`${input.id}Hint`; hint.setAttribute('aria-live','polite');
  // the first row's request fields, readable without a pointer (a title is not)
  const preview=document.createElement('details'); preview.className='path-preview'; preview.hidden=true;
  preview.innerHTML=html`<summary>${t('First row')}</summary><pre></pre>`.text;
  (input.closest('label')||input).after(hint,preview);
  input.setAttribute('aria-describedby',[input.getAttribute('aria-describedby'),hint.id].filter(Boolean).join(' '));
  let timer=null, sequence=0;
  const run=async()=>{
    const path=input.value.trim(), current=++sequence;
    if(!path){hint.textContent='';hint.className='path-hint';preview.hidden=true;return;}
    try{
      const r=await api('/api/paths/check',{method:'POST',body:JSON.stringify({path,kind})});
      if(current!==sequence||input.value.trim()!==path)return;  // a newer check, or the field changed since
      hint.className=`path-hint ${r.ok?'ok':'problem'}`;
      hint.textContent=r.ok?`✓ ${pathSummary(r)}`:PATH_PROBLEMS[r.code]||t('This path cannot be used.');
      hint.title=r.path;
      preview.hidden=!r.first?.preview; preview.querySelector('pre').textContent=r.first?.preview||'';
    }catch(e){if(current===sequence&&input.value.trim()===path){hint.className='path-hint problem';hint.textContent=e.message;preview.hidden=true;}}
  };
  // typing invalidates a check still in flight: its answer is for a path no longer in the field
  input.addEventListener('input',()=>{sequence++;clearTimeout(timer);timer=setTimeout(run,400);});
  // code that sets the value (a loaded setting, a cleared field) dispatches 'pathcheck' to refresh the hint now
  input.addEventListener('pathcheck',()=>{clearTimeout(timer);run();});
  if(input.value.trim())run();
}
// DOMContentLoaded: after every deferred script ran, so every view script has built its fields (Calibration's are
// rendered by manager.js, after this file)
document.addEventListener('DOMContentLoaded',()=>{
  attachPathCheck($('#batchInput'),'jsonl'); attachPathCheck($('#batchOutput'),'output');
  attachPathCheck($('#calibrationInput'),'jsonl'); attachPathCheck($('#calibrationImportPath'),'json');
  attachPathCheck($('#serveLog'),'append');  // basal-serve appends to its decision log
  attachPathCheck($('#modelsDir'),'dir');
});
