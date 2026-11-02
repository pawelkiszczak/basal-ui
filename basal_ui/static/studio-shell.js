'use strict';

// Studio preferences share the real controls and save handlers; only appearance is browser-local.
window.StudioShell = (() => {
  const categories = ['storage', 'defaults', 'appearance'];
  let category = 'storage';
  let railOpen = matchMedia('(min-width: 1100px)').matches;
  const narrow = () => matchMedia('(max-width: 1099px)').matches;
  function applySkin(value) {
    const skin = value === 'orange' ? 'orange' : 'green';
    document.documentElement.dataset.skin = skin;
    localStorage.setItem('basal-ui.skin', skin);
    document.querySelectorAll('[data-skin-choice]').forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.skinChoice === skin));
    });
  }
  function setRail(open, focus = false) {
    railOpen = open;
    document.body.classList.toggle('quick-actions-open', open);
    $('#quickActionsRail').hidden = !open;
    $('#toggleQuickActions').setAttribute('aria-expanded', String(open));
    if (focus) (open ? $('#quickActionSearch') : $('#toggleQuickActions')).focus();
  }
  function selectCategory(value) {
    if (categories.includes(value)) category = value;
    for (const key of categories) {
      const active = key === category;
      const button = $(`#settings-tab-${key}`);
      button.setAttribute('aria-selected', String(active));
      button.tabIndex = active ? 0 : -1;
      $(`#settings-${key}`).hidden = !active;
    }
  }
  function onView(name, arg) {
    const button = $('#openSettings');
    button.classList.toggle('active', name === 'settings');
    if (name === 'settings') { button.setAttribute('aria-current', 'page'); selectCategory(arg); }
    else button.removeAttribute('aria-current');
  }
  function init() {
    applySkin(localStorage.getItem('basal-ui.skin'));
    document.querySelectorAll('[data-skin-choice]').forEach(button => {
      button.onclick = () => applySkin(button.dataset.skinChoice);
    });
    $('#openSettings').onclick = () => showView(`settings/${category}`, true);
    const autoLoad = $('#autoLoad');
    const label = autoLoad.closest('label');
    label.lastChild.textContent = ` ${t('Auto-load remembered models')}`;
    $('#autoloadSetting').append(label);
    $('#saveDefaults').onclick = async () => {
      const button = $('#saveDefaults'), error = $('#defaultsError');
      button.disabled = autoLoad.disabled = true;
      try {
        serveData = await api('/api/serve', {method:'PUT', body:JSON.stringify({auto_load:autoLoad.checked})});
        error.hidden = true;
        toast(t('Defaults saved'));
      } catch (reason) { error.textContent = reason.message; error.hidden = false; }
      finally { button.disabled = autoLoad.disabled = false; }
    };
    const tabs = categories.map(key => $(`#settings-tab-${key}`));
    tabs.forEach((button, index) => {
      button.onclick = () => showView(`settings/${button.dataset.settingsCategory}`, true);
      button.onkeydown = event => {
        if (event.altKey || event.ctrlKey || event.metaKey) return;
        const offset = {ArrowDown:1, ArrowRight:1, ArrowUp:-1, ArrowLeft:-1}[event.key];
        if (offset === undefined && !['Home','End'].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + offset + tabs.length) % tabs.length;
        tabs[next].focus(); tabs[next].click();
      };
    });
    const actions = [
      ['load', 'Load downloaded model', 'Choose a downloaded model and its load options.'],
      ['playground', 'Open Playground', 'Edit a request and review its Decision.'],
      ['code', 'API examples', 'Copy the current request for your client.'],
      ['settings', 'Settings', 'Memory, storage, defaults, and appearance.'],
      ['batch', 'Batch paths', 'Inspect local request and output paths.'],
      ['manager', 'Model manager', 'Manage downloaded and loaded models.'],
    ];
    const rail = $('#quickActionsRail');
    rail.innerHTML = `<div class="quick-actions-heading"><h2>${t('Quick actions')}</h2><button type="button" id="closeQuickActions" aria-label="${t('Close quick actions')}">×</button></div><label for="quickActionSearch">${t('Filter actions')}</label><input id="quickActionSearch" type="search" placeholder="${t('Filter actions…')}" autocomplete="off"><div class="quick-action-list">${actions.map(([key, title, detail]) => `<button type="button" data-quick-action="${key}"><strong>${esc(t(title))}</strong><small>${esc(t(detail))}</small></button>`).join('')}</div><p id="quickActionsEmpty" class="muted" hidden>${t('No matching actions.')}</p><p class="quick-actions-hint"><kbd>Ctrl/⌘ K</kbd> · ${t('Filter and navigate with arrow keys.')}</p>`;
    const search = $('#quickActionSearch');
    const buttons = [...rail.querySelectorAll('[data-quick-action]')];
    const visible = () => buttons.filter(button => !button.hidden);
    search.oninput = () => {
      const query = search.value.trim().toLocaleLowerCase(LANG);
      buttons.forEach(button => { button.hidden = !button.textContent.toLocaleLowerCase(LANG).includes(query); });
      $('#quickActionsEmpty').hidden = visible().length > 0;
    };
    search.onkeydown = event => {
      if (['ArrowDown','ArrowUp','Enter'].includes(event.key)) {
        event.preventDefault();
        const list = visible();
        const target = event.key === 'ArrowUp' ? list.at(-1) : list[0];
        if (event.key === 'Enter') target?.click(); else target?.focus();
      }
    };
    buttons.forEach(button => {
      button.onclick = () => {
        const action = button.dataset.quickAction;
        if (narrow()) setRail(false);
        if (action === 'load') $('#openModelLoader').click();
        else if (action === 'code') { showView('playground', true); window.RequestCode.open(); }
        else showView(action === 'settings' ? `settings/${category}` : action, true);
      };
      button.onkeydown = event => {
        if (!['ArrowDown','ArrowUp','Home','End'].includes(event.key)) return;
        event.preventDefault();
        const list = visible(), index = list.indexOf(button);
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? list.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + list.length) % list.length;
        list[next]?.focus();
      };
    });
    $('#toggleQuickActions').onclick = () => setRail(!railOpen, true);
    $('#closeQuickActions').onclick = () => setRail(false, true);
    document.addEventListener('keydown', event => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k' && !document.querySelector('dialog[open]')) {
        event.preventDefault(); setRail(true, true);
      } else if (event.key === 'Escape' && railOpen && rail.contains(document.activeElement)) {
        event.preventDefault(); setRail(false, true);
      }
    });
    $('#shortcutsDialog h3').insertAdjacentHTML('afterend', `<p><kbd>Ctrl/⌘ + K</kbd> ${t('Open quick actions')}</p>`);
    setRail(railOpen);
    onView(location.hash.slice(1).split('/')[0], location.hash.split('/')[1]);
  }
  document.addEventListener('DOMContentLoaded', init);
  return {onView, applySkin};
})();
