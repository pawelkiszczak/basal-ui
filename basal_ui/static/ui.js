'use strict';
// Shared presentation primitives. These never call APIs or change application state.
function uiEscape(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function uiIcon(name) {
  return `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><use href="/static/vendor/lucide.svg#${uiEscape(name)}"></use></svg>`;
}
function emptyState({icon='box', title, description='', action='', href='', compact=false, inline=false}) {
  return `<div class="empty-state${compact?' compact':''}${inline?' inline':''}"><span class="empty-icon">${uiIcon(icon)}</span><strong class="empty-title">${uiEscape(t(title))}</strong>${description?`<p>${uiEscape(t(description))}</p>`:''}${action&&href?`<a class="action-link${compact?'':' primary'}" href="${uiEscape(href)}">${uiEscape(t(action))}${uiIcon('arrow-right')}</a>`:''}</div>`;
}
// Use the same library for the existing navigation and static action controls.
const navigationIcons={overview:'house',models:'layers',manager:'hard-drive',backends:'cpu',playground:'flask-conical',batch:'list-video',inspect:'scan-search',api:'server',history:'history',logs:'terminal'};
for(const [view,icon] of Object.entries(navigationIcons)) {
  const button=document.querySelector(`.side-nav [data-view="${view}"]`);
  button?.querySelector('svg')?.remove();
  button?.insertAdjacentHTML('afterbegin',uiIcon(icon));
}
const actionIcons={refreshModels:'refresh-cw',refreshBackends:'refresh-cw',sendButton:'play',runBatch:'play',startInspect:'scan-search',saveServe:'save',restartServe:'refresh-cw',saveMemory:'save',saveModelsDir:'save',createKey:'key-round',copyLogs:'copy',downloadLogs:'download',clearLogs:'trash-2',clearHistory:'trash-2',exportPresets:'download',importPresetsButton:'upload',downloadBatch:'download',copyBatchPath:'copy',copyServeConnect:'copy',copyServeUrl:'copy'};
for(const [id,icon] of Object.entries(actionIcons))document.getElementById(id)?.insertAdjacentHTML('afterbegin',uiIcon(icon));
