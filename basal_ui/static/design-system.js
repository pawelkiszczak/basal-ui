'use strict';
// Optional catalogue. Remove this script tag to remove its navigation, page and route.
// Tokens, primitives and application behavior live in app.css and ui.js, independently.
window.basalExtraPages={...(window.basalExtraPages||{}),'design-system':['','Design System','The foundations and components used throughout basal.']};
const dsNav=document.createElement('button');
dsNav.type='button'; dsNav.dataset.view='design-system';
dsNav.innerHTML=uiIcon('palette')+t('Design System');
document.querySelector('.side-nav').append(dsNav);
const dsPage=document.createElement('section');
dsPage.className='view'; dsPage.id='design-system'; dsPage.dataset.page='design-system'; dsPage.hidden=true;
const dsSwatches=[['Canvas','--paper'],['Surface','--white'],['Raised surface','--surface-raised'],['Text','--ink'],['Secondary text','--muted'],['Border','--line'],['Brand','--accent'],['Success','--green'],['Warning','--amber'],['Error','--danger'],['Information','--info'],['Focus','--focus']];
dsPage.innerHTML=`
<nav class="ds-nav" aria-label="Design system sections">${['Principles','Foundations','Components','Patterns','States'].map(name=>`<button class="ghost" data-ds-scroll="ds-${name.toLowerCase()}">${t(name)}</button>`).join('')}</nav>
<section class="ds-section" id="ds-principles"><h2>${t('Quiet structure. Clear actions.')}</h2><p>${t('A local workbench for decisions. Every screen uses the same spacing, controls and semantic colors. No decorative labels, numbered headings or nested panels without a purpose.')}</p>
<div class="ds-grid"><div class="card"><h3>${t('Layout and content')}</h3>
<div class="ds-rule"><strong>${t('One clear next step')}</strong><p>${t('Use a filled button for the main action. Use outlined buttons for secondary actions and text links for navigation.')}</p></div>
<div class="ds-rule"><strong>${t('Space shows relationships')}</strong><p>${t('4 px base. 8 px between controls, 12 px within a group, 20–24 px between groups, 40 px between catalogue sections.')}</p></div>
<div class="ds-rule"><strong>${t('Put help where it helps')}</strong><p>${t('Lead with the task. Keep labels short. Move file formats, engine details and long explanations into disclosures.')}</p></div></div>
<div class="card"><h3>${t('Interaction and access')}</h3>
<div class="ds-rule"><strong>${t('Color has a job')}</strong><p>${t('Orange identifies the brand and main action. Green means success, amber needs attention, red means error, blue informs. Always pair color with text or an icon.')}</p></div>
<div class="ds-rule"><strong>${t('Every state belongs')}</strong><p>${t('Empty states explain what will appear and how to begin. Disabled controls explain their prerequisite. Errors preserve input and offer recovery.')}</p></div>
<div class="ds-rule"><strong>${t('Keyboard, light and dark')}</strong><p>${t('Visible focus, labelled controls, reduced motion and layouts that wrap. Light and dark themes use the same semantic tokens.')}</p></div></div></div></section>
<section class="ds-section" id="ds-foundations"><h2>${t('Foundations')}</h2><p>${t('These are live application tokens. Change the theme in the sidebar to inspect both palettes.')}</p>
<div class="ds-swatches">${dsSwatches.map(([name,token])=>`<div class="ds-swatch"><span style="--swatch:var(${token})"></span><strong>${t(name)}</strong><code>${token}</code></div>`).join('')}</div>
<div class="ds-grid" style="margin-top:20px"><div class="card"><h3>${t('Typography')}</h3>
<div class="ds-type"><h1>${t('Make a decision')}</h1><small>Manrope · 28 / 35 · 750</small></div>
<div class="ds-type"><h2>${t('Section heading')}</h2><small>Manrope · 21 / 28 · 700</small></div>
<div class="ds-type"><h3>${t('Component title')}</h3><small>Manrope · 15 / 22 · 700</small></div>
<div class="ds-type"><p>${t('Body text stays readable and direct.')}</p><small>Manrope · 14 / 22 · 400</small></div>
<div class="ds-type"><small>${t('Labels and supporting information')}</small><small>Manrope · 12 / 19</small></div>
<div class="ds-type"><code>127.0.0.1:8000 · 32 ms</code><small>IBM Plex Mono · 12 / 20 · 400</small></div></div>
<div class="card"><h3>${t('Spacing and shape')}</h3>${[4,8,12,16,20,24,32,40,48].map(n=>`<div class="ds-space"><code>${String(n).padStart(2,'0')} px</code><span style="--size:${n*3}px"></span></div>`).join('')}<p class="form-hint">${t('Controls: 36 px. Mobile actions: 40 px. Corners: 6 px controls, 10 px panels, 14 px dialogs.')}</p></div></div></section>
<section class="ds-section" id="ds-components"><h2>${t('Components')}</h2><p>${t('Real shared classes and primitives, with isolated example data. Examples do not call the API or change your models.')}</p>
<div class="ds-grid"><div class="card"><h3>${t('Actions')}</h3><div class="controls"><button class="primary" data-ds-action>${uiIcon('download')}${t('Download')}</button><button data-ds-action>${uiIcon('sliders-horizontal')}${t('Setup')}</button><button class="destructive" data-ds-action>${uiIcon('trash-2')}${t('Delete')}</button><button disabled>${uiIcon('play')}${t('Load')}</button></div><p class="form-hint">${t('Action icons lead; navigation arrows follow. Tabs and short mode choices use text only.')}</p><p id="ds-action-feedback" class="form-hint" aria-live="polite"></p></div>
<div class="card"><h3>${t('Status')}</h3><div class="chips"><span class="badge success">${uiIcon('circle-check')}${t('Ready')}</span><span class="badge warning">${uiIcon('triangle-alert')}${t('Tight memory')}</span><span class="badge error">${uiIcon('circle-x')}${t('Failed')}</span><span class="badge info">${uiIcon('info')}${t('Information')}</span><span class="chip">MLX 8-bit</span></div><p class="form-hint" style="margin-top:16px">${t('Status is semantic. Model formats and metadata stay neutral.')}</p></div>
<div class="card"><h3>${t('Form controls')}</h3><div class="field"><label for="ds-model">${t('Model')}</label><select id="ds-model"><option>basal-2.0 mini · MLX 8-bit</option><option>basal-omni pico · bf16</option></select></div><div class="field"><label for="ds-name">${t('Name')}</label><input id="ds-name" placeholder="${t('Give this request a name')}"></div><div class="field"><label for="ds-disabled">${t('Unavailable model')}</label><select id="ds-disabled" disabled><option>${t('Load a model first')}</option></select></div><label style="margin-top:16px"><input type="checkbox" role="switch"> ${t('Include evidence')}</label></div>
<div class="card"><h3>${t('Tabs and disclosure')}</h3><div class="tabs" role="tablist" aria-label="${t('Example tabs')}"><button role="tab" id="ds-tab-suggested" aria-controls="ds-tab-panel" class="active" aria-selected="true" data-ds-tab="suggested">${t('Suggested')}</button><button role="tab" id="ds-tab-all" aria-controls="ds-tab-panel" aria-selected="false" data-ds-tab="all" tabindex="-1">${t('All models')}</button></div><p id="ds-tab-panel" role="tabpanel" aria-labelledby="ds-tab-suggested" class="form-hint">${t('Models chosen for this machine.')}</p><details class="technical-help"><summary>${t('Technical details')}</summary><p>${t('Keep secondary information here so the task stays easy to scan.')}</p></details><div class="ds-rule"><h3>${t('Dialogs')}</h3><p>${t('A clear title, useful context and an explicit action. Escape and Cancel close without changes.')}</p><button id="ds-open-dialog" style="margin-top:12px">${t('Open example dialog')}</button></div></div></div>
<div class="card" style="margin-top:20px"><h3>${t('Iconography')}</h3><p class="form-hint" style="margin-bottom:16px">${t('Locally bundled Lucide. 16 px actions, 18 px navigation, 21 px empty states. One stroke weight; no emoji or text glyphs.')}</p><div class="ds-icons">${['layers','cpu','hard-drive','flask-conical','server','terminal','download','sliders-horizontal','history','key-round'].map(name=>`<span>${uiIcon(name)}${name}</span>`).join('')}</div></div>
<div class="card" style="margin-top:20px"><h3>${t('Data table')}</h3><div class="table-wrap"><table><thead><tr><th>${t('Model')}</th><th>${t('Mode')}</th><th>${t('Memory')}</th><th>${t('Status')}</th></tr></thead><tbody><tr><td>basal-2.0 mini</td><td><span class="chip">MLX 8-bit</span></td><td>3.2 GB</td><td><span class="badge success">${t('Ready')}</span></td></tr><tr><td>basal-omni pico</td><td><span class="chip">bf16</span></td><td>2.9 GB</td><td><span class="chip">${t('On disk')}</span></td></tr></tbody></table></div></div></section>
<section class="ds-section" id="ds-patterns"><h2>${t('Patterns')}</h2><p>${t('Compose screens from the same model card, metrics, toolbar and feedback patterns.')}</p>
<div class="ds-grid"><div id="ds-model-card"></div><div class="card"><h3>${t('Hardware metrics')}</h3><div class="hardware-grid" style="padding:0;border:0;margin-top:20px"><div class="metric"><label>${t('Platform')}</label><strong>Apple</strong></div><div class="metric"><label>${t('Memory budget')}</label><strong>12.7 GB</strong></div><div class="metric"><label>${t('Available now')}</label><strong>6.4 GB</strong></div><div class="metric"><label>${t('System RAM')}</label><strong>17.2 GB</strong></div></div><div style="margin-top:24px"><div class="memory-head"><span>${t('Memory')}</span><span>3.2 GB / 10.6 GB</span></div><div class="progress"><span style="width:30%"></span></div></div></div>
<div class="card"><h3>${t('Work toolbar')}</h3><div class="work-toolbar"><div class="tabs"><button class="active" data-ds-action>basal-ui</button></div><label><input type="checkbox" checked> ${t('Follow')}</label><button data-ds-action>${uiIcon('copy')}${t('Copy')}</button><button data-ds-action>${uiIcon('download')}${t('Download')}</button></div></div>
<div class="card"><h3>${t('Feedback')}</h3><p class="form-hint">${t('Keep operation feedback short. Errors stay beside the input that needs attention.')}</p><div class="ds-feedback toast success" style="margin-top:16px">${uiIcon('circle-check')} ${t('Example action completed. No application data changed.')}</div></div></div>
<div class="card" style="margin-top:20px"><h3>${t('Inline state')}</h3>${emptyState({icon:'layers',title:'Ready for your first model',description:'Choose a model that fits your machine, then load it to start making decisions.',action:'Browse models',href:'#models',inline:true})}</div></section>
<section class="ds-section" id="ds-states"><h2>${t('States')}</h2><p>${t('Use the same empty-state primitive in collections, panels and summaries. Keep the message specific and the next step honest.')}</p><div class="ds-grid"><div class="card">${emptyState({icon:'layers',title:'Ready for your first model',description:'Choose a model that fits your machine, then load it to start making decisions.',action:'Browse models',href:'#models'})}</div><div class="card">${emptyState({icon:'history',title:'Your decisions, in one place',description:'Run a request in the Playground. Its input, answer and timing will appear here.',action:'Open Playground',href:'#playground'})}</div><div class="card"><h3>${t('Compact state')}</h3>${emptyState({icon:'server',title:'API is switched off',description:'Use models in this app, or enable the API to connect your own tools.',compact:true})}</div><div class="card"><h3>${t('Loading and error')}</h3><div class="skeleton" role="status" aria-label="${t('Loading…')}"></div><div class="error-box" role="status">${uiIcon('triangle-alert')} ${t('Could not read the file. Check its path and try again.')}</div></div></div></section>
<dialog id="ds-dialog" aria-labelledby="ds-dialog-title"><h2 id="ds-dialog-title">${t('Example dialog')}</h2><p>${t('This example is isolated. Confirming changes only this preview.')}</p><div class="dialog-actions"><button type="button" data-ds-close>${t('Cancel')}</button><button type="button" class="primary" data-ds-close>${t('Confirm')}</button></div></dialog>
`;
document.querySelector('main').append(dsPage);
document.addEventListener('DOMContentLoaded',()=>{
  const model={family:'basal-2.0',tier:'mini',label:'MLX 8-bit',weights_bytes:1.8e9,est_memory_bytes:3.2e9,fit_now:'fits'};
  const suggestion={slot:'recommended',why:t('A compact model for everyday decisions on Apple Silicon.')};
  document.getElementById('ds-model-card').innerHTML=renderSuggestionCard(suggestion,model,`<div class="controls"><button class="primary" data-ds-action>${uiIcon('download')}${t('Download')}</button><button data-ds-action>${uiIcon('sliders-horizontal')}${t('Setup')}</button></div>`,`<button type="button" class="icon-only ds-model-info" aria-label="${t('Model information')}" data-ds-action>${uiIcon('info')}</button>`);
});
dsPage.addEventListener('click',event=>{
  const scroll=event.target.closest('[data-ds-scroll]');
  if(scroll)document.getElementById(scroll.dataset.dsScroll)?.scrollIntoView({behavior:'auto',block:'start'});
  if(event.target.closest('[data-ds-action]'))document.getElementById('ds-action-feedback').textContent=t('Example action completed. No application data changed.');
  const tab=event.target.closest('[data-ds-tab]');
  if(tab)selectDsTab(tab);
  if(event.target.closest('#ds-open-dialog'))document.getElementById('ds-dialog').showModal();
  if(event.target.closest('[data-ds-close]'))document.getElementById('ds-dialog').close();
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
