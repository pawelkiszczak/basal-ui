'use strict';
// Shared presentation primitives. They never call APIs or change application state; app.js, backends.js and the
// optional catalogue (design-system.js) render through the same functions.
function uiEscape(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function uiIcon(name) {
  return `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><use href="/static/vendor/lucide.svg#${uiEscape(name)}"></use></svg>`;
}
// One empty-state anatomy: icon, short title, one helpful sentence, at most one next step (a link or a button that
// the owning view handles through data-empty-action). compact: inside a summary; inline: a row inside a panel.
function emptyState({icon='box', title, description='', action='', href='', button='', compact=false, inline=false}) {
  const label=uiEscape(t(action));
  const next=action&&href?`<a class="action-link${compact||inline?' sm':' primary'}" href="${uiEscape(href)}">${label}${uiIcon('arrow-right')}</a>`
    :action&&button?`<button type="button" class="${compact||inline?'sm':''}" data-empty-action="${uiEscape(button)}">${label}</button>`:'';
  return `<div class="empty-state${compact?' compact':''}${inline?' inline':''}"><span class="empty-icon">${uiIcon(icon)}</span><strong class="empty-title">${uiEscape(t(title))}</strong>${description?`<p>${uiEscape(t(description))}</p>`:''}${next}</div>`;
}
// A short contextual message. text is already translated; tone is info, success, warning, error or neutral.
function uiNotice({tone='info', icon='', text='', action='', href=''}) {
  const icons={info:'info',success:'circle-check',warning:'triangle-alert',error:'circle-x',neutral:'info'};
  return `<div class="notice notice-${tone}">${uiIcon(icon||icons[tone])}<span>${uiEscape(text)}</span>${action&&href?`<a class="dashboard-link" href="${uiEscape(href)}">${uiEscape(action)}${uiIcon('arrow-right')}</a>`:''}</div>`;
}
// A status label: tone and shape carry the state, the word names it (success, warning, error, info, neutral).
function uiBadge(tone, label, icon='') {
  return `<span class="badge ${uiEscape(tone)}">${icon?uiIcon(icon):''}${uiEscape(label)}</span>`;
}
// The same library for navigation and the static actions of index.html: action icons lead their label.
const navigationIcons={overview:'house',models:'layers',manager:'hard-drive',backends:'cpu',playground:'flask-conical',batch:'list-video',inspect:'scan-search',api:'server',history:'history',logs:'terminal'};
for(const [view,icon] of Object.entries(navigationIcons)) {
  const button=document.querySelector(`.side-nav [data-view="${view}"]`);
  button?.querySelector('svg')?.remove();
  button?.insertAdjacentHTML('afterbegin',uiIcon(icon));
}
const actionIcons={refreshModels:'refresh-cw',refreshBackends:'refresh-cw',sendButton:'play',runBatch:'play',cancelBatch:'square',startInspect:'scan-search',stopInspect:'square',saveServe:'save',restartServe:'rotate-ccw',saveMemory:'save',saveModelsDir:'save',defaultModelsDir:'rotate-ccw',createKey:'plus',copyLogs:'copy',downloadLogs:'download',clearLogs:'x',clearHistory:'trash-2',exportPresets:'download',importPresetsButton:'upload',downloadBatch:'download',copyBatchPath:'copy',copyServeConnect:'copy',copyServeUrl:'copy',addQuestion:'plus',clearBackendModelFilter:'x',footerUnload:'power'};
for(const [id,icon] of Object.entries(actionIcons))document.getElementById(id)?.insertAdjacentHTML('afterbegin',uiIcon(icon));
// In-page section navigation: scrolls without touching the hash (the hash is the view router).
document.addEventListener('click',event=>{
  const target=event.target.closest('[data-scroll-to]');
  if(target)document.getElementById(target.dataset.scrollTo)?.scrollIntoView({block:'start'});
});
