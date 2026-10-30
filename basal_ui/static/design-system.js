'use strict';
// Optional catalogue. Remove this script tag to remove its navigation, page and route; no application view uses it.
// Every example renders through the application's own classes and primitives (app.css, ui.js, renderSuggestionCard)
// with local example data. Examples never call the API, start downloads, load models or change saved settings, and
// they avoid the action classes app.js binds globally (load-model, download-model, delete-model, options-model, ...).
window.basalExtraPages={...(window.basalExtraPages||{}),'design-system':['','Design System','Tokens, controls and compositions used throughout basal, rendered live.']};
const dsNav=document.createElement('button');
dsNav.type='button'; dsNav.dataset.view='design-system';
dsNav.innerHTML=uiIcon('palette')+uiEscape(t('Design System'));
document.querySelector('.side-nav').append(dsNav);
const dsPage=document.createElement('section');
dsPage.className='view'; dsPage.id='design-system'; dsPage.dataset.page='design-system'; dsPage.hidden=true;

const dsAct=(label,icon='',cls='')=>`<button type="button"${cls?` class="${cls}"`:''} data-ds-action>${icon?uiIcon(icon):''}${uiEscape(t(label))}</button>`;
const dsHead=(id,title,text)=>`<header><h2 id="${id}-title">${uiEscape(t(title))}</h2><p>${uiEscape(t(text))}</p></header>`;
const dsCard=(title,body,note='')=>`<div class="card"><span class="ds-label">${uiEscape(t(title))}</span>${body}${note?`<p class="ds-note" style="margin-top:12px">${uiEscape(t(note))}</p>`:''}</div>`;
const dsMark=`<svg class="brand-mark" viewBox="0 0 36 36" aria-hidden="true"><path d="m18 3 16 8-16 8L2 11Z" fill="currentColor"/><path d="m2 18 16 8 16-8M2 25l16 8 16-8" fill="none" stroke="currentColor" stroke-width="3.5"/></svg>`;
// A specimen scopes data-skin and data-theme to itself: the same tokens resolve to that combination inside it.
const dsSpecimen=(skin,theme)=>`<div class="ds-specimen" data-skin="${skin}" data-theme="${theme}" inert>
<div class="ds-specimen-head"><strong>${uiEscape(t(skin==='green'?'Green':'Orange'))} · ${uiEscape(t(theme==='light'?'Light':'Dark'))}</strong><span class="brand">${dsMark}<span class="brand-name">basal<span class="brand-dot">.</span></span></span></div>
<nav class="side-nav"><button type="button" class="active">${uiIcon('house')}${uiEscape(t('Overview'))}</button><button type="button">${uiIcon('layers')}${uiEscape(t('Models'))}</button></nav>
<div class="ds-row"><button type="button" class="primary">${uiIcon('download')}${uiEscape(t('Download'))}</button><button type="button">${uiIcon('sliders-horizontal')}${uiEscape(t('Setup'))}</button><button type="button" class="destructive">${uiIcon('trash-2')}${uiEscape(t('Delete'))}</button></div>
<div class="ds-row">${uiBadge('success',t('Ready'))}${uiBadge('warning',t('tight'))}${uiBadge('error',t('Failed'))}${uiBadge('info',t('Information'))}</div>
<div class="ds-row"><label><input type="checkbox" checked> ${uiEscape(t('Facts'))}</label><a href="#design-system">${uiEscape(t('Release notes'))}</a><button type="button" class="sm ds-focus">${uiEscape(t('Focus'))}</button></div>
</div>`;
const dsSwatches=[['Canvas','--paper'],['Surface','--white'],['Subtle surface','--surface-subtle'],['Text','--ink'],['Secondary text','--muted'],['Border','--line'],['Control border','--line-strong'],['Brand','--accent'],['Brand fill','--accent-solid'],['Brand tint','--accent-light'],['Success','--green'],['Warning','--amber'],['Error','--danger'],['Information','--info'],['Focus','--focus']];
const dsType=[['h1','Make a decision','24 / 31 · 720'],['h2','Section heading','19 / 26 · 700'],['h3','Panel title','15 / 22 · 700'],['p','Body text stays readable and direct.','14 / 21 · 400'],['small','Labels, hints and table text','12 – 13 / 18 · 400–650'],['code','127.0.0.1:8000 · 32 ms','IBM Plex Mono · 12 / 20']];
const dsTable=`<div class="table-wrap"><table><thead><tr><th>${uiEscape(t('Model'))}</th><th>${uiEscape(t('Mode'))}</th><th>${uiEscape(t('Memory'))}</th><th>${uiEscape(t('Status'))}</th></tr></thead><tbody><tr><td>basal-2.0 mini</td><td><span class="chip">MLX 8-bit</span></td><td>3.2 GB</td><td>${uiBadge('success',t('Ready'))}</td></tr><tr><td>basal-omni pico</td><td><span class="chip">bf16</span></td><td>2.9 GB</td><td>${uiBadge('neutral',t('On disk'))}</td></tr><tr><td>basal-2.0 max</td><td><span class="chip">MLX 4-bit</span></td><td>11.4 GB</td><td>${uiBadge('warning',t('tight'))}</td></tr></tbody></table></div>`;

dsPage.innerHTML=`
<nav class="ds-nav section-nav" aria-label="${uiEscape(t('Design system sections'))}">${[['ds-skins','Skins'],['ds-foundations','Foundations'],['ds-controls','Controls'],['ds-compositions','Compositions'],['ds-states','States']].map(([id,name])=>`<button type="button" data-scroll-to="${id}">${uiEscape(t(name))}</button>`).join('')}<span class="toolbar-spacer"></span><span id="ds-feedback" class="state-line" aria-live="polite"></span></nav>

<section class="ds-section" id="ds-skins" aria-labelledby="ds-skins-title">${dsHead('ds-skins','Skins and themes','Green is the default skin; orange is the original warm basal palette. A skin changes the brand and neutral tint only. Status, destructive actions and focus keep their meaning in every skin, and light and dark themes work with both.')}
<div class="ds-specimens">${dsSpecimen('green','light')}${dsSpecimen('green','dark')}${dsSpecimen('orange','light')}${dsSpecimen('orange','dark')}</div>
<p class="ds-note">${uiEscape(t('Choose the skin, theme and language at the bottom of the sidebar. Each is stored separately in this browser and applied before the first paint; an unknown skin falls back to green.'))}</p>
</section>

<section class="ds-section" id="ds-foundations" aria-labelledby="ds-foundations-title">${dsHead('ds-foundations','Foundations','Live tokens of the current skin and theme. Views use these roles, never raw colors.')}
<div class="ds-swatches">${dsSwatches.map(([name,token])=>`<div class="ds-swatch"><span style="--swatch:var(${token})"></span><strong>${uiEscape(t(name))}</strong><code>${token}</code></div>`).join('')}</div>
<div class="ds-grid">
${dsCard('Typography',dsType.map(([tag,text,spec])=>`<div class="ds-type"><${tag}>${uiEscape(t(text))}</${tag}><small>${spec}</small></div>`).join(''))}
<div class="card"><span class="ds-label">${uiEscape(t('Spacing'))}</span><div class="ds-scale">${[4,8,12,16,20,24,32,40].map(n=>`<div><code>${n} px</code><span style="--size:${n*4}px"></span></div>`).join('')}</div>
<span class="ds-label" style="margin-top:20px">${uiEscape(t('Shape and size'))}</span><div class="ds-shapes"><div><span style="border-radius:4px;width:44px;height:20px"></span>4 px</div><div><span style="border-radius:6px"></span>6 px</div><div><span style="border-radius:10px"></span>10 px</div><div><span style="border-radius:12px"></span>12 px</div></div>
<p class="ds-note" style="margin-top:12px">${uiEscape(t('4 px badges, 6 px controls, 10 px panels, 12 px dialogs. Controls are 34 px, compact row actions 28 px, and 38 px below 600 px.'))}</p></div>
</div>
</section>

<section class="ds-section" id="ds-controls" aria-labelledby="ds-controls-title">${dsHead('ds-controls','Controls','The shared controls in every state. Buttons here only report that they were pressed.')}
<div class="ds-grid">
${dsCard('Buttons',`<div class="ds-row">${dsAct('Run batch','play','primary')}${dsAct('Setup','sliders-horizontal')}${dsAct('Copy','copy','ghost')}${dsAct('Delete','trash-2','destructive')}</div><div class="ds-row">${dsAct('Unload','power','sm')}${dsAct('Reset','','sm')}<button type="button" class="icon-only" aria-label="${uiEscape(t('Model information'))}" data-ds-action>${uiIcon('info')}</button><button type="button" disabled>${uiIcon('play')}${uiEscape(t('Load'))}</button><button type="button" class="primary" disabled>${uiIcon('download')}${uiEscape(t('Downloading…'))}</button></div>`,'One filled action per task. Outlined buttons for secondary actions, a red outline to enter a destructive flow and a red fill only to confirm it.')}
${dsCard('Fields',`<div class="field"><label for="ds-name">${uiEscape(t('Key name'))}</label><input id="ds-name" placeholder="${uiEscape(t('Give this request a name'))}"><small>${uiEscape(t('Create a named key for each app that connects to this server.'))}</small></div><div class="field"><label for="ds-port">${uiEscape(t('Port'))}</label><input id="ds-port" type="number" value="80" aria-invalid="true"><small class="status-error">${uiEscape(t('Use a port from 1024 to 65535.'))}</small></div><div class="field"><label for="ds-input-file">${uiEscape(t('Input file'))}</label><div class="input-icon">${uiIcon('file-input')}<input id="ds-input-file" placeholder="/path/to/requests.jsonl"></div></div>`)}
${dsCard('Selection',`<div class="field"><label for="ds-model">${uiEscape(t('Model'))}</label><select id="ds-model"><option>basal-2.0 mini · MLX 8-bit</option><option>basal-omni pico · bf16</option></select></div><div class="field"><label for="ds-disabled">${uiEscape(t('Model'))}</label><select id="ds-disabled" disabled><option>${uiEscape(t('Load a model first'))}</option></select><small class="field-hint">${uiEscape(t('Batch runs on a loaded model.'))} <a href="#design-system">${uiEscape(t('Load a model'))}</a></small></div>`,'A disabled control always explains its prerequisite next to it.')}
${dsCard('Choices',`<div class="ds-row"><label><input type="checkbox" role="switch" checked> ${uiEscape(t('Follow'))}</label><label><input type="checkbox" role="switch"> ${uiEscape(t('Facts'))}</label><label><input type="checkbox" role="switch" disabled> ${uiEscape(t('MCP at /mcp'))}</label></div><div class="ds-row"><label><input type="radio" name="ds-radio" checked> ${uiEscape(t('shipped default'))}</label><label><input type="radio" name="ds-radio"> ${uiEscape(t('imported'))}</label></div><div class="ds-row"><div class="tabs" role="tablist" aria-label="${uiEscape(t('Example tabs'))}"><button type="button" role="tab" id="ds-tab-suggested" aria-controls="ds-tab-panel" class="active" aria-selected="true" data-ds-tab="suggested">${uiEscape(t('Suggested'))}</button><button type="button" role="tab" id="ds-tab-all" aria-controls="ds-tab-panel" aria-selected="false" data-ds-tab="all" tabindex="-1">${uiEscape(t('All models'))}</button></div></div><p id="ds-tab-panel" role="tabpanel" aria-labelledby="ds-tab-suggested" class="ds-note" style="margin-top:8px">${uiEscape(t('Models chosen for this machine.'))}</p>`,'Switches apply immediately; segmented tabs switch between views of the same content.')}
${dsCard('Status',`<div class="chips">${uiBadge('success',t('Ready'))}${uiBadge('warning',t('Needs attention'))}${uiBadge('error',t('Failed'))}${uiBadge('info',t('Information'))}${uiBadge('neutral',t('Not installed'))}</div><div class="chips" style="margin-top:12px"><span class="chip">MLX 8-bit</span><span class="chip media">image</span><span class="badge rec">${uiEscape(t('recommended'))}</span><span class="state-pill">${uiEscape(t('starting'))}</span></div><div class="chips" style="margin-top:12px"><span class="status-dot ready"></span>${uiEscape(t('ready'))}<span class="status-dot ready active"></span>${uiEscape(t('answering'))}<span class="status-dot error"></span>${uiEscape(t('error'))}<span class="status-dot"></span>${uiEscape(t('idle'))}</div>`,'A state has a shape, a color and a word: circle success, triangle attention, octagon error, ring information or neutral. Metadata chips stay neutral.')}
${dsCard('Notices and progress',`<div class="ds-stack">${uiNotice({tone:'info',text:t('Load a model to enable the playground.'),action:t('Load a model'),href:'#design-system'})}${uiNotice({tone:'success',text:t('Inference test passed')})}${uiNotice({tone:'warning',text:t('Local-network access without a required API key is not recommended.')})}<div class="error-box">${uiEscape(t('Could not read the file. Check its path and try again.'))}</div><div><div class="memory-head"><span>${uiEscape(t('Memory'))}</span><span>3.2 GB / 10.6 GB</span></div><div class="progress memory-progress"><span style="width:30%"></span></div></div></div>`)}
</div>
${dsCard('Icons',`<div class="ds-icons">${['house','layers','hard-drive','cpu','flask-conical','list-video','scan-search','server','history','terminal','download','play','square','power','rotate-ccw','sliders-horizontal','copy','trash-2','key-round','flame','apple','zap','gauge','info','circle-check','triangle-alert','circle-x'].map(name=>`<span>${uiIcon(name)}${name}</span>`).join('')}</div>`,'Locally bundled Lucide, 1.75 px stroke: 16 px in controls, 18 px in navigation, 20 px in identity and empty states. Action icons lead their label; navigation arrows follow it.')}
</section>

<section class="ds-section" id="ds-compositions" aria-labelledby="ds-compositions-title">${dsHead('ds-compositions','Compositions','Screens are assembled from these patterns. Each is the markup the application renders, with example data.')}
${dsCard('Work toolbar',`<div class="toolbar"><label class="toolbar-field"><span>${uiEscape(t('Model'))}</span><select aria-label="${uiEscape(t('Model'))}"><option>basal-2.0 mini · MLX 8-bit</option></select></label><div class="tabs" role="group" aria-label="${uiEscape(t('Source'))}"><button type="button" class="active" data-ds-action>${uiEscape(t('All'))}</button><button type="button" data-ds-action>${uiEscape(t('Playground'))}</button><button type="button" data-ds-action>API</button></div><span class="toolbar-spacer"></span>${dsAct('Copy','copy','sm')}${dsAct('Clear history','trash-2','destructive sm')}</div>`,'Filters and view choices start at the left; actions share the right group. The bar wraps instead of clipping.')}
<div class="card hardware"><div class="hardware-identity"><span class="identity-icon">${uiIcon('cpu')}</span><div class="hardware-heading"><div class="hardware-name">Apple M5</div><div class="chips"><span class="chip">mlx</span><span class="chip">mps</span></div></div></div><dl class="hardware-grid"><div class="metric"><dt>${uiEscape(t('Platform'))}</dt><dd>Apple</dd></div><div class="metric"><dt>${uiEscape(t('Memory budget'))}</dt><dd>12.7 GB</dd></div><div class="metric"><dt>${uiEscape(t('Available now'))}</dt><dd>6.4 GB</dd></div><div class="metric"><dt>${uiEscape(t('System RAM'))}</dt><dd>17.2 GB</dd></div></dl></div>
<div class="overview-grid">
<div class="overview-main">
<section class="card panel"><div class="panel-head"><h3 class="panel-title">${uiEscape(t('Loaded models'))}</h3><a class="dashboard-link" href="#design-system">${uiEscape(t('Model manager'))}${uiIcon('arrow-right')}</a></div>
<div class="activity"><div class="inference-row"><span class="status-dot ready active"></span><strong>Remek/basal-2.0-mini</strong><span class="muted">2 ${uiEscape(t('running'))} · Playground 1 · ${uiEscape(t('API server'))} 1</span></div></div>
<div class="memory-head"><span>${uiEscape(t('Memory'))}</span><span>3.2 GB / 10.6 GB</span></div><div class="progress memory-progress"><span style="width:30%"></span></div>
<div style="margin-top:12px"><div class="model-row"><div class="model-row-main"><span class="status-dot ready active"></span><strong>Remek/basal-2.0-mini</strong>${uiBadge('success',t('ready'))}<span class="chip">${uiEscape(t('idle'))}</span></div><div class="chips"><span class="chip">mlx</span><span class="chip">orders 2</span></div><div class="muted model-row-meta">3.2 GB → 3.0 GB · 128 · 92.4%</div><div class="model-row-actions">${dsAct('Restart','rotate-ccw')}${dsAct('Options','sliders-horizontal')}${dsAct('Unload','power')}</div></div></div>
</section>
<div id="ds-model-card" class="suggestion-grid"></div>
</div>
<aside class="overview-side">
<section class="card summary-card"><div class="summary-head"><span class="summary-icon">${uiIcon('hard-drive')}</span><h3>${uiEscape(t('On disk'))}</h3></div><div class="summary-value">${uiEscape(t('No models yet'))}</div><div class="summary-meta">${uiEscape(t('Download a model that fits this machine. It stays available offline.'))}</div><a class="dashboard-link" href="#design-system">${uiEscape(t('Browse models'))}${uiIcon('arrow-right')}</a></section>
<section class="card summary-card"><div class="summary-head"><span class="summary-icon">${uiIcon('server')}</span><h3>${uiEscape(t('API server'))}</h3></div><div class="summary-value"><span class="status-dot"></span>${uiEscape(t('Off'))}</div><div class="summary-meta">${uiEscape(t('Use models in this app, or enable the API to connect your own tools.'))}</div><a class="dashboard-link" href="#design-system">${uiEscape(t('Configure'))}${uiIcon('arrow-right')}</a></section>
</aside>
</div>
<section class="card panel"><div class="panel-head"><div class="panel-heading"><h3 class="panel-title">${uiEscape(t('On disk'))}</h3><p class="panel-sub">${uiEscape(t('A panel owns one task: a heading with its actions, divided rows, and settings in the footer.'))}</p></div>${dsAct('Browse models','arrow-right','ghost sm')}</div>
<div class="disk-group"><div class="disk-group-heading"><h4>basal-2.0</h4><span class="muted">2 · 4.1 GB</span></div>
<div class="disk-row"><div class="disk-info"><strong>mini · MLX 8-bit</strong><span class="muted">1.8 GB</span></div><div class="disk-actions">${uiBadge('success',t('loaded'))}${dsAct('Unload','power')}</div></div>
<div class="disk-row"><div class="disk-info"><strong>max · MLX 4-bit</strong><span class="muted">2.3 GB</span></div><div class="disk-actions">${dsAct('Load','play','primary')}${dsAct('Options','sliders-horizontal')}</div></div></div>
<div class="panel-foot"><div class="field" style="flex:1 1 320px"><label for="ds-dir">${uiEscape(t('Models directory'))}</label><div class="directory-row"><div class="input-icon">${uiIcon('folder-open')}<input id="ds-dir" value="~/.cache/huggingface"></div>${dsAct('Save','save')}</div></div></div>
</section>
${dsCard('Data table',dsTable,'Tables own one bordered surface with a quiet header; rows stay readable at 12.5 px and scroll inside their own container.')}
</section>

<section class="ds-section" id="ds-states" aria-labelledby="ds-states-title">${dsHead('ds-states','States','Empty, filtered, loading, failed and disabled are different conditions with different messages. Size each to its context.')}
<div class="ds-grid">
<div class="card">${emptyState({icon:'history',title:'No decisions recorded yet',description:'Decisions from the Playground, the API server and MCP appear here with their request, answers and latency. Open one to copy it or replay it in the Playground.',action:'Open Playground',button:'ds'})}</div>
<div class="card">${emptyState({icon:'search',title:'No matching decisions',description:'Try another filter or search term.',action:'Clear filters',button:'ds'})}</div>
${dsCard('Inline state',emptyState({icon:'play',title:'No model is loaded',description:'Load a downloaded model to start making decisions.',action:'Load a model',button:'ds',inline:true}))}
${dsCard('Loading and failure',`<div class="skeleton" role="status" aria-label="${uiEscape(t('Loading…'))}"></div><div class="error-box">${uiEscape(t('Could not read the file. Check its path and try again.'))}</div><div class="toast" style="margin-top:12px;animation:none">${uiEscape(t('Example action completed. No application data changed.'))}</div>`,'Loading keeps the final layout. Errors stay next to the input that needs attention; toasts confirm a finished action.')}
</div>
${dsCard('Dialogs',`<button type="button" id="ds-open-dialog">${uiEscape(t('Open example dialog'))}</button>`,'A clear title, the consequence, and an action named after its effect. Escape and Cancel close without changes.')}
</section>
<dialog id="ds-dialog" aria-labelledby="ds-dialog-title"><h3 id="ds-dialog-title">${uiEscape(t('Delete model from disk?'))}</h3><p>${uiEscape(t('This example is isolated. Confirming changes only this preview.'))}</p><div class="dialog-actions"><button type="button" data-ds-close>${uiEscape(t('Cancel'))}</button><button type="button" class="destructive" data-ds-close>${uiEscape(t('Delete'))}</button></div></dialog>
`;
document.querySelector('main').append(dsPage);
document.addEventListener('DOMContentLoaded',()=>{
  // the application's suggestion renderer with example data and catalogue-only actions
  const model={family:'basal-2.0',tier:'mini',label:'MLX 8-bit',weights_bytes:1.8e9,est_memory_bytes:3.2e9,fit_now:'fits'};
  const suggestion={slot:'recommended',why:t('A compact model for everyday decisions on Apple Silicon.')};
  document.getElementById('ds-model-card').innerHTML=renderSuggestionCard(suggestion,model,`<div class="controls">${dsAct('Download','download','primary')}${dsAct('Setup','sliders-horizontal')}</div>`,`<button type="button" class="icon-only" aria-label="${uiEscape(t('Model information'))}" data-ds-action>${uiIcon('info')}</button>`);
});
dsPage.addEventListener('click',event=>{
  if(event.target.closest('[data-ds-action],[data-empty-action="ds"]'))document.getElementById('ds-feedback').textContent=t('Example action completed. No application data changed.');
  const tab=event.target.closest('[data-ds-tab]');
  if(tab)selectDsTab(tab);
  if(event.target.closest('#ds-open-dialog'))document.getElementById('ds-dialog').showModal();
  if(event.target.closest('[data-ds-close]'))document.getElementById('ds-dialog').close();
  if(event.target.closest('a[href="#design-system"]'))event.preventDefault();
});
function selectDsTab(tab) {
  dsPage.querySelectorAll('[data-ds-tab]').forEach(button=>{const active=button===tab;button.classList.toggle('active',active);button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;});
  const panel=document.getElementById('ds-tab-panel');
  panel.setAttribute('aria-labelledby',tab.id);
  panel.textContent=t(tab.dataset.dsTab==='all'?'All model formats, grouped by family.':'Models chosen for this machine.');
}
dsPage.addEventListener('keydown',event=>{
  if(!event.target.matches('[data-ds-tab]')||!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
  event.preventDefault();
  const tabs=[...dsPage.querySelectorAll('[data-ds-tab]')],index=tabs.indexOf(event.target);
  const next=tabs[event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length];
  selectDsTab(next);next.focus();
});
