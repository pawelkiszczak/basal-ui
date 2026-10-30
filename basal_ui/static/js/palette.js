// The command palette (Ctrl/⌘+K): views, models and actions. A combobox input owns a listbox of options; the arrows,
// Home and End move the active option (aria-activedescendant), Enter runs it, Escape closes the dialog.
'use strict';
const PALETTE_MAC=/Mac|iPhone|iPad/.test(navigator.platform||navigator.userAgent);
let paletteItems=[], paletteActive=0;
function paletteCommands() {
  const go=Object.entries(PAGES).map(([view,[,title]])=>({group:t('Views'),label:t('Go to {view}',{view:t(title)}),keys:`${view} ${viewHash(view)}`,run:()=>showView(view,true)}));
  const ready=readyModels(), loaded=statusCache?.models||[];
  const models=[
    ...ready.map(m=>({group:t('Models'),label:t('Use {model} in the Playground',{model:shortName(m.id)}),keys:m.id,run:()=>useInPlayground(m.id)})),
    ...loaded.map(m=>({group:t('Models'),label:t('Unload {model}',{model:shortName(m.id)}),keys:m.id,run:()=>act(()=>unloadOne(m.id))})),
    ...modelData.filter(m=>m.downloaded&&m.runnable&&!loaded.some(x=>x.id===m.id)).map(m=>({group:t('Models'),label:t('Load {model}',{model:shortName(m.id)}),keys:m.id,run:()=>loadModel(m.id)})),
    ...(catalogData?.suggestions||[]).map(s=>modelData.find(m=>m.id===s.id)).filter(m=>m&&!m.downloaded&&m.runnable&&fitNow(m)!=='too_large'&&!loaded.some(x=>x.id===m.id))
      .map(m=>({group:t('Models'),label:t('Download & load {model}',{model:shortName(m.id)}),keys:m.id,run:()=>loadModel(m.id)})),
  ];
  const actions=[
    {group:t('Actions'),label:t('Open settings'),keys:'preferences options',run:()=>showView('settings',true)},
    {group:t('Actions'),label:t('Run a benchmark'),keys:'evaluate benchmarks latency accuracy library',run:()=>showView('benchmarks',true)},
    {group:t('Actions'),label:t('Calibrate a model'),keys:'evaluate calibration thresholds library',run:()=>showView('calibration',true)},
    {group:t('Actions'),label:t('Toggle theme'),keys:'dark light mode',run:toggleTheme},
    {group:t('Actions'),label:t('Copy API endpoint'),keys:'url server connect',run:()=>act(()=>copyEndpoint())},
    ...(loaded.length?[{group:t('Actions'),label:t('Unload all'),keys:'stop models',run:()=>act(unloadAll)}]:[]),
    {group:t('Actions'),label:t('Keyboard shortcuts'),keys:'help keys',run:openShortcuts},
  ];
  return [...actions,...models,...go];
}
function renderPalette() {
  const words=$('#paletteInput').value.toLocaleLowerCase().split(/\s+/).filter(Boolean);
  const matches=paletteCommands().filter(c=>words.every(w=>`${c.label} ${c.keys} ${c.group}`.toLocaleLowerCase().includes(w)));
  // ranked: a word of the label starting with the query, then the label containing it, then a keyword; groups by their best
  const label=c=>c.label.toLocaleLowerCase(), query=words.join(' ');
  const score=c=>!query?0:label(c).split(/\s+/).some(w=>w.startsWith(words[0]))&&label(c).includes(query)?0:label(c).includes(query)?1:2;
  const best=new Map(); matches.forEach(c=>best.set(c.group,Math.min(best.get(c.group)??3,score(c))));
  const order=[...best.keys()];
  paletteItems=matches.map((c,i)=>({c,i})).sort((x,y)=>best.get(x.c.group)-best.get(y.c.group)||order.indexOf(x.c.group)-order.indexOf(y.c.group)||score(x.c)-score(y.c)||x.i-y.i).map(x=>x.c);
  paletteActive=Math.min(paletteActive,Math.max(0,paletteItems.length-1));
  let group='';
  $('#paletteList').innerHTML=paletteItems.length?paletteItems.map((c,i)=>{
    const head=c.group!==group?html`<li role="presentation" class="palette-group">${c.group}</li>`:'';
    group=c.group;
    return html`${head}<li role="option" id="palette-option-${i}" class="palette-option" data-index="${i}" aria-selected="${i===paletteActive}">${c.label}</li>`.text;
  }).join(''):html`<li role="presentation" class="palette-empty muted">${t('No matching command')}</li>`.text;
  syncPaletteActive();
}
function syncPaletteActive() {
  const input=$('#paletteInput');
  $('#paletteList').querySelectorAll('[role=option]').forEach(o=>o.setAttribute('aria-selected',String(Number(o.dataset.index)===paletteActive)));
  const active=paletteItems.length?$(`#palette-option-${paletteActive}`):null;
  if(active){input.setAttribute('aria-activedescendant',active.id);active.scrollIntoView({block:'nearest'});}
  else input.removeAttribute('aria-activedescendant');
}
function openPalette() {
  const dialog=$('#commandPalette');
  if(dialog.open||document.querySelector('dialog[open]'))return;  // never over another dialog's unsaved work
  $('#paletteInput').value=''; paletteActive=0; renderPalette();
  dialog.showModal(); $('#paletteInput').focus();
}
function runPalette(index) {
  const command=paletteItems[index];
  if(!command)return;
  $('#commandPalette').close(); command.run();
}
$('#paletteHint').textContent=t('↑ ↓ move · Enter runs · Esc closes');
$('#paletteOpen kbd').textContent=PALETTE_MAC?'⌘K':'Ctrl K';
$('#paletteOpen').onclick=openPalette;
$('#paletteInput').addEventListener('input',()=>{paletteActive=0;renderPalette();});
$('#paletteInput').addEventListener('keydown',e=>{
  const last=paletteItems.length-1, next={ArrowDown:paletteActive+1,ArrowUp:paletteActive-1,Home:0,End:last}[e.key];
  if(e.key==='Enter'){e.preventDefault();runPalette(paletteActive);return;}
  if(next===undefined||last<0||((e.key==='Home'||e.key==='End')&&!e.ctrlKey&&$('#paletteInput').value))return;  // Home/End edit the text
  e.preventDefault(); paletteActive=(next+last+1)%(last+1); syncPaletteActive();
});
$('#paletteList').addEventListener('click',e=>{const o=e.target.closest('[role=option]');if(o)runPalette(Number(o.dataset.index));});
$('#paletteList').addEventListener('mousemove',e=>{const o=e.target.closest('[role=option]');if(o&&Number(o.dataset.index)!==paletteActive){paletteActive=Number(o.dataset.index);syncPaletteActive();}});
$('#commandPalette').addEventListener('click',e=>{if(e.target===e.currentTarget)e.currentTarget.close();});  // the backdrop
document.addEventListener('keydown',e=>{
  if(!(e.ctrlKey||e.metaKey)||e.altKey||e.shiftKey||e.key.toLowerCase()!=='k')return;
  if(PALETTE_MAC&&!e.metaKey&&e.target instanceof Element&&e.target.matches('input,textarea,[contenteditable],[contenteditable] *'))return;  // Ctrl+K: kill line
  e.preventDefault(); openPalette();
});
