/* THROWAWAY: three templates on the real SPA route, selected by ?variant=A|B|C.
 * Same hardware/catalog and request across variants. No real mutation or inference is allowed.
 * Loaded only by scripts/design_preview.py; production index.html never includes this file.
 */
(() => {
  Object.assign(PL, {
    'Appearance & language': 'Wygląd i język',
    'Card support': 'Obsługa kart',
    'Design prototype': 'Prototyp wyglądu',
    'Read-only design prototype. Hardware and catalog are live; Demo traffic and sample Decisions are illustrative.': 'Prototyp tylko do odczytu. Sprzęt i katalog są rzeczywiste; ruch Demo i przykładowe Decyzje są ilustracją.',
    'Focus': 'Skupienie',
    'Illustrative only — no inference has run.': 'Wyłącznie ilustracja — nie wykonano inferencji.',
    'Layout sample — no model loaded': 'Przykład układu — brak załadowanego modelu',
    'Machine & activity': 'Sprzęt i aktywność',
    'Next design': 'Następny projekt',
    'Original': 'Oryginał',
    'Other options': 'Pozostałe opcje',
    'Previous design': 'Poprzedni projekt',
    'Preview sample Decision': 'Pokaż przykładową Decyzję',
    'Read-only preview: edit the sample request freely; no model will run.': 'Podgląd tylko do odczytu: edytuj przykład; żaden model nie zostanie uruchomiony.',
    'Request settings': 'Ustawienia żądania',
    'Sample Decision': 'Przykładowa Decyzja',
    'Selected option': 'Wybrana opcja',
    'Studio': 'Studio',
    'Tools & settings': 'Narzędzia i ustawienia',
    'Workspace': 'Przestrzeń robocza',
  });
  const query = selector => document.querySelector(selector);
  const design = document.body.dataset.design;
  const variants = ['A', 'B', 'C'];
  const names = {A: 'Workspace', B: 'Studio', C: 'Focus'};
  const urlFor = key => {
    const url = new URL(location.href);
    url.searchParams.set('variant', key);
    if (key === 'original' && url.hash === '#dashboard') url.hash = '#overview';
    if (key !== 'original' && design === 'original' && url.hash === '#overview') url.hash = '#dashboard';
    return url.href;
  };
  const notice = document.createElement('div');
  notice.className = 'prototype-notice';
  notice.textContent = t('Read-only design prototype. Hardware and catalog are live; Demo traffic and sample Decisions are illustrative.');
  document.body.prepend(notice);

  const switcher = document.createElement('nav');
  switcher.className = 'prototype-switcher';
  switcher.setAttribute('aria-label', t('Design prototype'));
  switcher.innerHTML = `<button type="button" data-cycle="-1" aria-label="${t('Previous design')}">←</button>
    ${variants.map(key => `<a href="${esc(urlFor(key))}" aria-current="${design === key}">${key}<span> · ${t(names[key])}</span></a>`).join('')}
    <button type="button" data-cycle="1" aria-label="${t('Next design')}">→</button>
    <a class="prototype-original" href="${esc(urlFor('original'))}" aria-current="${design === 'original'}">${t('Original')}</a>`;
  document.body.append(switcher);
  function updateSwitcherLinks() {
    switcher.querySelectorAll('a').forEach((link, index) => link.href = urlFor(index < variants.length ? variants[index] : 'original'));
  }
  window.addEventListener('hashchange', updateSwitcherLinks);
  const cycle = direction => {
    const index = variants.indexOf(design);
    const next = index < 0 ? (direction > 0 ? 0 : variants.length - 1) : (index + direction + variants.length) % variants.length;
    location.assign(urlFor(variants[next]));
  };
  switcher.querySelectorAll('[data-cycle]').forEach(button => button.onclick = () => cycle(Number(button.dataset.cycle)));
  document.addEventListener('keydown', event => {
    if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey ||
        event.target.closest('input,textarea,select,[contenteditable],[role="tablist"],dialog') ||
        document.querySelector('dialog[open]')) return;
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault();
      cycle(event.key === 'ArrowRight' ? 1 : -1);
    }
  });

  // The sample is explicitly illustrative, not a model answer, timing or confidence measurement.
  const request = {
    state: 'A customer’s card was blocked after three incorrect PIN attempts. They need help unlocking it before travelling tomorrow.',
    questions: {
      department: {
        type: 'choice',
        instructions: 'Which team should handle this request?',
        criteria: {cards: 'Card support', loans: 'Loans and financing', online: 'Online banking'},
      },
    },
  };
  setRequest(request);
  function showSample() {
    query('#results').innerHTML = `<p class="prototype-sample-note">${t('Illustrative only — no inference has run.')}</p>
      <article class="prototype-result"><span class="chip">${t('Sample Decision')}</span>
      <strong>${t('Card support')}</strong><p class="muted">${esc(request.questions.department.instructions)}</p>
      <dl><dt>${t('Question')}</dt><dd><code>department</code></dd>
      <dt>${t('Type')}</dt><dd><code>choice</code></dd>
      <dt>${t('Selected option')}</dt><dd><code>cards</code></dd></dl>
      <details><summary>${t('Other options')}</summary><p class="muted">Loans and financing · Online banking</p></details></article>`;
    query('#rawDetails').hidden = true;
  }
  showSample();
  // Keep editing local even though the real API correctly reports that no model is loaded.
  const originalModels = renderPlaygroundModels;
  renderPlaygroundModels = function(ready) {
    originalModels(ready);
    query('#playgroundForm').disabled = false;
    query('#sendButton').disabled = false;
    query('#sendButton').textContent = t('Preview sample Decision');
    query('#playgroundHint').textContent = t('Read-only preview: edit the sample request freely; no model will run.');
    if (!ready.length) query('#playgroundModel').replaceChildren(new Option(t('Layout sample — no model loaded'), ''));
  };
  renderPlaygroundModels([]);
  document.addEventListener('click', event => {
    if (event.target.closest('#sendButton')) {
      event.preventDefault();
      event.stopImmediatePropagation();
      showSample();
      if (design === 'C') setFocus('decision');
    }
  }, true);
  if (design === 'original') return;

  // Keep the real DOM nodes: navigation, form state, dialogs and all existing handlers survive.
  const nav = query('.side-nav');
  const primary = document.createElement('div');
  primary.className = 'prototype-primary-nav';
  const caption = document.createElement('div');
  caption.className = 'prototype-nav-caption';
  caption.textContent = t('Workspace');
  const tools = document.createElement('details');
  tools.className = 'prototype-tools';
  tools.innerHTML = `<summary>${t('Tools & settings')}</summary><div></div>`;
  for (const button of [...nav.children]) {
    (['dashboard', 'models', 'playground', 'history'].includes(button.dataset.view) ? primary : tools.lastElementChild).append(button);
    if (button.classList.contains('active') && button.parentElement === tools.lastElementChild) tools.open = true;
  }
  nav.append(caption, primary, tools);
  nav.addEventListener('click', event => {
    if (event.target.closest('[data-view]')) {
      updateSwitcherLinks();
      if (design === 'B') tools.open = false;
      window.scrollTo(0, 0);
    }
  });
  const sidebarStatus = query('.sidebar-status');
  const appearance = document.createElement('details');
  appearance.className = 'prototype-appearance';
  appearance.innerHTML = `<summary>${t('Appearance & language')}</summary><div></div>`;
  for (const node of [...sidebarStatus.children]) {
    if (!node.matches('.footer-status,#footerModel,#footerUrl,#footerUnload')) appearance.lastElementChild.append(node);
  }
  sidebarStatus.append(appearance);

  const toolbar = query('.playground-head');
  const options = document.createElement('details');
  options.className = 'prototype-request-settings';
  options.innerHTML = `<summary>${t('Request settings')}</summary><div class="prototype-settings-content"></div>`;
  for (const node of [...toolbar.children]) {
    if (!node.matches('#playgroundModel,#examples')) options.lastElementChild.append(node);
  }
  toolbar.append(options);

  function WorkspaceTemplate() {
    // A: persistent sidebar, hardware strip, asymmetric dashboard and two-panel playground.
    query('#overview').setAttribute('aria-label', t('Workspace'));
  }
  function StudioTemplate() {
    // B: horizontal navigation, full-width canvas, paired dashboard and equal request/Decision panes.
    query('#overview').setAttribute('aria-label', t('Studio'));
  }
  function FocusTemplate() {
    // C: a single reading column; diagnostics are secondary and request/Decision use separate tabs.
    const diagnostics = document.createElement('details');
    diagnostics.className = 'prototype-diagnostics';
    diagnostics.innerHTML = `<summary>${t('Machine & activity')}</summary><div></div>`;
    diagnostics.lastElementChild.append(query('#hardware'), query('#inference'));
    query('#overview').append(diagnostics);
    const tabs = document.createElement('div');
    tabs.className = 'prototype-focus-tabs';
    tabs.setAttribute('role', 'tablist');
    tabs.setAttribute('aria-label', t('Playground'));
    tabs.innerHTML = `<button type="button" role="tab" id="prototype-request-tab" data-focus="request" aria-controls="prototype-request">01 · ${t('Request')}</button>
      <button type="button" role="tab" id="prototype-decision-tab" data-focus="decision" aria-controls="prototype-decision">02 · ${t('Decision')}</button>`;
    query('.playground-workspace').before(tabs);
    for (const [selector, key] of [['.request-panel', 'request'], ['.response-panel', 'decision']]) {
      const panel = query(selector);
      panel.id = `prototype-${key}`;
      panel.setAttribute('role', 'tabpanel');
      panel.setAttribute('aria-labelledby', `prototype-${key}-tab`);
    }
    tabs.querySelectorAll('button').forEach(button => button.onclick = () => setFocus(button.dataset.focus));
    tabs.addEventListener('keydown', event => {
      if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
        event.preventDefault();
        const key = document.body.dataset.focus === 'request' ? 'decision' : 'request';
        setFocus(key);
        query(`#prototype-${key}-tab`).focus();
      }
    });
    setFocus('request');
  }
  function setFocus(key) {
    document.body.dataset.focus = key;
    document.querySelectorAll('.prototype-focus-tabs button').forEach(button => {
      const selected = button.dataset.focus === key;
      button.setAttribute('aria-selected', String(selected));
      button.tabIndex = selected ? 0 : -1;
    });
  }
  ({A: WorkspaceTemplate, B: StudioTemplate, C: FocusTemplate})[design]();
})();
