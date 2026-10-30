/* THROWAWAY refinement of the selected Studio layout. Move existing nodes, never clone controls.
 * Library is operational; model usage shares Dashboard Live counters. No new polling or recording.
 */
(() => {
  if (document.body.dataset.design !== 'B') return;
  Object.assign(PL, {
    'Library': 'Biblioteka',
    'Usage by model': 'Użycie według modeli',
    'Live inventory': 'Rzeczywisty stan modeli',
    'Loaded models': 'Załadowane modele',
    'Downloaded models': 'Pobrane modele',
    'Weight files on disk': 'Pliki wag na dysku',
    'Storage location': 'Lokalizacja modeli',
    'Custom dataset': 'Własny zbiór danych',
    'Labelled requests': 'Żądania z oznaczonymi odpowiedziami',
    'How calibration works': 'Jak działa kalibracja',
    'How this measurement runs': 'Jak przebiega ten pomiar',
    'Open Dashboard Live': 'Otwórz pulpit z rzeczywistymi licznikami',
    'Load and organize models. Usage and tuning stay out of the way.': 'Ładuj i porządkuj modele. Użycie i dostrajanie mają osobne zakładki.',
    'Live model counters, not a second set of statistics.': 'Rzeczywiste liczniki modeli, a nie drugi zestaw statystyk.',
    'These are the same recorded Decision counters used by Dashboard Live. Viewing either page does not add requests.': 'To te same liczniki zapisanych Decyzji, których używa pulpit w trybie rzeczywistym. Wyświetlanie stron nie dodaje żądań.',
    'Resets are shared: resetting a model or all statistics also changes Dashboard Live. Dashboard Demo data is illustrative and independent.': 'Reset jest wspólny: wyzerowanie statystyk modelu lub wszystkich statystyk zmienia też rzeczywiste liczniki pulpitu. Dane Demo są przykładowe i niezależne.',
    'Benchmark results are separate from recorded Decision traffic.': 'Wyniki benchmarków są oddzielne od ruchu zapisanych Decyzji.',
  });
  const q = selector => document.querySelector(selector);
  const manager = q('#manager');
  const disk = manager.querySelector('.disk-list');
  const storageField = q('#modelsDir').closest('.field');
  const loaded = q('#status');
  const stats = q('#statsTable');
  const benchmarks = q('#benchmarks');
  const calibration = q('#calibrationBlock');
  const tabs = [['library','Library'],['usage','Usage by model'],['benchmarks','Benchmarks'],['calibration','Calibration']];

  const summary = document.createElement('div');
  summary.className = 'prototype-manager-summary';
  summary.innerHTML = `<div><span>${t('Loaded models')}</span><strong id="prototype-manager-loaded">—</strong></div>
    <div><span>${t('Downloaded models')}</span><strong id="prototype-manager-downloaded">—</strong></div>
    <div><span>${t('Weight files on disk')}</span><strong id="prototype-manager-bytes">—</strong></div>
    <span class="prototype-manager-live">${t('Live inventory')}</span>`;
  const tablist = document.createElement('div');
  tablist.className = 'prototype-manager-tabs';
  tablist.setAttribute('role', 'tablist');
  tablist.setAttribute('aria-label', t('Model manager'));
  tablist.innerHTML = tabs.map(([id,label])=>`<button type="button" role="tab" id="prototype-manager-tab-${id}" data-manager-tab="${id}" aria-controls="prototype-manager-${id}" aria-selected="false">${t(label)}</button>`).join('');
  const panels = new Map(tabs.map(([id])=>{
    const panel = document.createElement('section');
    panel.id = `prototype-manager-${id}`;
    panel.className = 'prototype-manager-panel';
    panel.setAttribute('role', 'tabpanel');
    panel.setAttribute('aria-labelledby', `prototype-manager-tab-${id}`);
    panel.hidden = true;
    return [id,panel];
  }));
  manager.replaceChildren(summary, tablist, ...panels.values());

  disk.classList.add('card','prototype-manager-disk');
  const loadedHeading = document.createElement('h2');
  loadedHeading.textContent = t('Loaded models');
  loaded.querySelector('.section-index').replaceWith(loadedHeading);
  loaded.classList.add('prototype-manager-loaded');
  const storage = document.createElement('details');
  storage.className = 'card prototype-manager-storage';
  storage.innerHTML = `<summary>${t('Storage location')}</summary>`;
  storage.append(storageField);
  panels.get('library').append(disk, loaded, storage);

  panels.get('usage').classList.add('card');
  panels.get('usage').innerHTML = `<div class="prototype-manager-panel-heading"><div><h2>${t('Usage by model')}</h2><p>${t('Live model counters, not a second set of statistics.')}</p></div><a href="#dashboard" id="prototype-manager-live-link">${t('Open Dashboard Live')} →</a></div>
    <p class="prototype-manager-usage-note">${t('These are the same recorded Decision counters used by Dashboard Live. Viewing either page does not add requests.')}</p>`;
  panels.get('usage').append(stats);
  const resetNote = document.createElement('p');
  resetNote.className = 'prototype-manager-reset-note';
  resetNote.textContent = t('Resets are shared: resetting a model or all statistics also changes Dashboard Live. Dashboard Demo data is illustrative and independent.');
  panels.get('usage').append(resetNote);
  q('#prototype-manager-live-link').onclick = () => q('[data-traffic-source="live"]').click();

  benchmarks.classList.add('card');
  calibration.classList.add('card');
  panels.get('benchmarks').append(benchmarks);
  panels.get('calibration').append(calibration);
  const benchmarkNote = document.createElement('p');
  benchmarkNote.className = 'prototype-manager-usage-note';
  benchmarkNote.textContent = t('Benchmark results are separate from recorded Decision traffic.');
  benchmarks.querySelector('.section-head').after(benchmarkNote);

  function labelControl(id, label) {
    const control = q(`#${id}`);
    const field = document.createElement('div');
    field.className = 'prototype-manager-field';
    const caption = document.createElement('label');
    caption.htmlFor = id;
    caption.textContent = t(label);
    control.before(field);
    field.append(caption, control);
  }
  for (const [id,label] of [
    ['benchmarkKind','Measurement'],['benchmarkModel','Model to benchmark'],
    ['benchmarkSet','Benchmark set'],['benchmarkPath','Custom dataset'],
    ['calibrationModel','Model to calibrate'],['calibrationInput','Labelled requests'],
    ['calibrationImportPath','Calibration file to import'],
  ]) labelControl(id, label);

  function helpDisclosure(node, label) {
    const details = document.createElement('details');
    details.className = 'prototype-manager-help';
    details.innerHTML = `<summary>${t(label)}</summary>`;
    node.before(details);
    details.append(node);
  }
  helpDisclosure(q('#benchmarkHint'), 'How this measurement runs');
  helpDisclosure(calibration.querySelector(':scope > p'), 'How calibration works');

  function activate(id, updateUrl=false) {
    for (const [key,panel] of panels) {
      const selected = key === id;
      panel.hidden = !selected;
      const button = q(`#prototype-manager-tab-${key}`);
      button.setAttribute('aria-selected', String(selected));
      button.tabIndex = selected ? 0 : -1;
    }
    if (updateUrl) {
      const url = new URL(location.href);
      url.searchParams.set('managerTab', id);
      history.replaceState(null, '', url);
    }
  }
  tablist.onclick = event => {
    const button = event.target.closest('[data-manager-tab]');
    if (button) activate(button.dataset.managerTab, true);
  };
  tablist.onkeydown = event => {
    const keys = ['ArrowLeft','ArrowRight','Home','End'];
    if (!keys.includes(event.key)) return;
    event.preventDefault();
    const index = tabs.findIndex(([id])=>q(`#prototype-manager-tab-${id}`).getAttribute('aria-selected')==='true');
    const next = event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;
    activate(tabs[next][0], true);
    q(`#prototype-manager-tab-${tabs[next][0]}`).focus();
  };
  const initial = new URL(location.href).searchParams.get('managerTab');
  activate(panels.has(initial) ? initial : 'library');

  function updateInventory() {
    const downloaded = modelData.filter(model=>model.downloaded);
    for (const [id,value] of [
      ['loaded', String((statusCache?.models || []).length)],
      ['downloaded', String(downloaded.length)],
      ['bytes', gb(downloaded.reduce((sum,model)=>sum+(Number(model.downloaded_bytes)||0),0))],
    ]) {
      const node = q(`#prototype-manager-${id}`);
      if (node.textContent !== value) node.textContent = value;
    }
    // The reused empty-state link should now lead to this page's library, not back to itself.
    const jump = q('#loadedModels .empty a');
    if (jump) jump.onclick = event => {event.preventDefault();disk.scrollIntoView({block:'start'});};
  }
  const inventoryObserver = new MutationObserver(updateInventory);
  for (const node of [q('#diskModels'), q('#loadedModels')]) inventoryObserver.observe(node, {childList:true,subtree:true});
  updateInventory();
  PAGES.manager = ['03 / MODEL MANAGER','Model manager','Load and organize models. Usage and tuning stay out of the way.'];
  if (location.hash.split('/')[0] === '#manager') showView(location.hash);
})();
