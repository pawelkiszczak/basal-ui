// Navigation between views, the Discover tabs and memory settings.
'use strict';
// view → [eyebrow, title, description], in menu order; the Models group's views keep their first hashes as aliases
const PAGES={overview:['01 / OVERVIEW','Overview','Hardware, model readiness, and serving at a glance.'],playground:['02 / PLAYGROUND','Playground','Ask typed questions of a loaded model.'],batch:['03 / BATCH','Batch','Run a JSONL file of requests through a loaded model and export the answers.'],models:['04 / DISCOVER','Discover','Find a model that fits this machine, then download and load it in one step.'],manager:['05 / LIBRARY','Library','Your models on disk and loaded, with their statistics, benchmarks and calibration.'],backends:['06 / BACKENDS','Backends','Local inference engines, compatibility, setup and explicit verification.'],api:['07 / API SERVER','API server','One port for every loaded model, keys, MCP and the snippets to connect your app.'],inspect:['08 / INSPECT','Inspect','basal-viz: the prompt, the tokens, every layer and the calibrated decision.'],history:['09 / HISTORY','History','Every decision this app answered: playground, API and MCP.'],logs:['10 / LOGS','Logs','Output of each basal-serve and basal-ui’s own events.'],settings:['11 / SETTINGS','Settings','Theme, language, models directory, auto-load, memory, updates and presets.']};
const VIEW_HASH={models:'discover',manager:'library'}, VIEW_ALIAS={discover:'models',library:'manager',benchmarks:'manager',calibration:'manager'};
// Evaluate entries that live in the Library: #benchmarks and #calibration open it at their block
const VIEW_SECTION={benchmarks:'#benchmarks',calibration:'#calibrationBlock'};
const viewHash=view=>VIEW_HASH[view]||view;
let currentView=null;
// focus: move the focus to the view's title and announce it (the user navigated; not the first render)
function showView(hash, updateHash=false, focus=updateHash) {
  const raw=String(hash||'').replace(/^#/,''), slash=raw.indexOf('/');
  const head=slash<0?raw:raw.slice(0,slash), view=VIEW_ALIAS[head]||head, arg=slash<0?'':raw.slice(slash+1), name=PAGES[view]?view:'overview';
  const [eyebrow,title,description]=PAGES[name], number=eyebrow.split(' / ')[0];
  $('#pageEyebrow').textContent=LANG==='en'?eyebrow:`${number} / ${t(title).toLocaleUpperCase(LANG)}`; $('#pageTitle').textContent=t(title); $('#pageDescription').textContent=t(description);
  document.querySelectorAll('.view').forEach(v=>v.hidden=v.dataset.page!==name);
  document.querySelectorAll('.side-nav [data-view]').forEach(b=>{const active=b.dataset.view===name;b.classList.toggle('active',active);if(active)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');});
  revealNavItem(document.querySelector('.side-nav [aria-current=page]'));
  document.title=`${t(title)} · basal`;
  if(focus&&name!==currentView){$('#pageTitle').focus();announce(t(title));}
  currentView=name;
  const target=`#${viewHash(name)}${arg?`/${arg}`:''}`;
  if(updateHash&&location.hash!==target)history.pushState(null,'',target);
  else if(!updateHash&&location.hash&&location.hash!==target)history.replaceState(null,'',target);  // #models → #discover
  shownHash=location.hash;
  clearTimeout(historyTimer); clearTimeout(logTimer);
  const section=VIEW_SECTION[head]&&$(VIEW_SECTION[head]);
  if(section){const h=$('h3',section)||section;h.tabIndex=-1;h.focus({preventScroll:true});pinSection(h);}
  if(name==='backends')window.dispatchEvent(new Event('backends:view'));
  if(name==='history')loadHistory('reset');
  if(name==='logs'){
    let source=logSource; try{if(arg)source=decodeURIComponent(arg);}catch{}
    if(source!==logSource){logSource=source;logAfter=0;logGeneration=null;$('#logOutput').textContent='';}
    loadLogs();
  }
}
// #benchmarks / #calibration: the Library above the block renders when its data arrives and pushes it down, so the
// block is kept at the top until that settles (3 s) or the user scrolls, clicks or presses a key
let sectionPin=null;
function pinSection(heading) {
  sectionPin?.();
  const go=()=>heading.scrollIntoView({block:'start'}), observer=new ResizeObserver(go), events=['wheel','touchstart','keydown','pointerdown'];
  const stop=()=>{observer.disconnect();clearTimeout(timer);events.forEach(e=>removeEventListener(e,stop,true));sectionPin=null;};
  const timer=setTimeout(stop,3000);
  events.forEach(e=>addEventListener(e,stop,{capture:true,passive:true}));
  observer.observe(heading.closest('.view')); go(); sectionPin=stop;
}
// the compact header's and the toasts' heights, for scroll-padding (views.css): Tab and anchors keep clear of both
for(const [node,name] of [[$('.sidebar'),'--header-h'],[$('#toastStack'),'--toast-h']])
  new ResizeObserver(()=>document.documentElement.style.setProperty(name,`${node.offsetHeight}px`)).observe(node);
// the compact (phone/tablet) nav is a scrolling strip: bring the current view's tab into it, without scrolling the page
function revealNavItem(button) {
  const nav=button?.closest('.side-nav'); if(!nav||nav.scrollWidth<=nav.clientWidth)return;
  const r=button.getBoundingClientRect(), n=nav.getBoundingClientRect();
  if(r.left<n.left||r.right>n.right)nav.scrollLeft+=r.left-n.left-(n.width-r.width)/2;
}
const route=()=>{if(location.hash!==shownHash)showView(location.hash,false,true);};
$('.skip-link').onclick=e=>{e.preventDefault();$('#pageTitle').focus();};
document.querySelectorAll('.side-nav [data-view]').forEach(b=>b.onclick=()=>showView(b.dataset.view,true));
window.addEventListener('hashchange',route); window.addEventListener('popstate',route);
function selectModelsTab(tab){for(const x of ['suggested','all']){$(`#${x==='all'?'allModels':x}`).hidden=x!==tab;const b=$(`#${x==='all'?'allModelsTab':'suggestedTab'}`);b.classList.toggle('active',x===tab);b.setAttribute('aria-selected',String(x===tab));}localStorage.setItem('basal-ui.tab',tab);}
$('#suggestedTab').onclick=()=>selectModelsTab('suggested');$('#allModelsTab').onclick=()=>selectModelsTab('all');
document.querySelectorAll('[role=tablist]').forEach(tablist);
selectModelsTab(localStorage.getItem('basal-ui.tab')==='all'?'all':'suggested');
async function loadMemorySettings(){
  try{
    const d=await api('/api/memory'), budget=$('input#memoryBudget');
    budget.placeholder=t('Hardware budget: {size} GiB ({decimal})',{size:(d.hardware_budget_bytes/2**30).toFixed(1),decimal:gb(d.hardware_budget_bytes)});
    for(const [input,bytes] of [[budget,d.settings.budget_bytes],[$('#memoryReserve'),d.settings.reserve_bytes]]){
      input.value=bytes==null?'':String(Number((bytes/2**30).toFixed(3)));
      input.dataset.bytes=bytes==null?'':String(bytes); input.dataset.shown=input.value;  // an unedited field saves its exact bytes
    }
  }catch(e){$('#memoryError').textContent=e.message;$('#memoryError').hidden=false;}
}
// a wrong value is named by its field: one inline alert (no toast), the field aria-invalid and described by it
function memoryError(message, field) {
  const box=$('#memoryError'), fields=[$('input#memoryBudget'),$('#memoryReserve')];
  box.textContent=message||''; box.hidden=!message;
  for(const input of fields){
    const bad=Boolean(message)&&input===field;
    input.setAttribute('aria-invalid',String(bad));
    if(bad)input.setAttribute('aria-describedby','memoryError'); else input.removeAttribute('aria-describedby');
  }
}
$('#saveMemory').onclick=async()=>{
  const bytesOf=input=>{const value=input.value.trim();if(value===input.dataset.shown&&input.dataset.bytes!==undefined)return input.dataset.bytes===''?null:Number(input.dataset.bytes);return value===''?null:Math.round(Number(value)*2**30);};
  const budget=$('input#memoryBudget'), reserve=$('#memoryReserve'), b=bytesOf(budget), r=bytesOf(reserve)??0;
  if(b!=null&&!(b>=2**30))return memoryError(t('Budget: leave it empty or enter at least 1 GiB.'),budget);
  if(!(r>=0))return memoryError(t('Keep free: enter 0 GiB or more.'),reserve);
  try{
    await api('/api/memory',{method:'PUT',body:JSON.stringify({budget_bytes:b,reserve_bytes:r})});
    memoryError(''); toast(t('Memory settings saved')); await Promise.all([loadMemorySettings(),refreshStatus()]);
  }catch(e){memoryError(e.message,e.message.includes('reserve_bytes')?reserve:e.message.includes('budget_bytes')?budget:null);}
};
