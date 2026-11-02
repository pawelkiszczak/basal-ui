'use strict';

(() => {
  const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const supported = new Set(['choice', 'multi', 'act']);
  const labels = {
    add: 'Add option', show: 'Show option keys', key: 'Option key', description: 'Description', options: 'Options',
    moveUp: 'Move option up', moveDown: 'Move option down', remove: 'Remove option',
    invalidKey: 'Option keys must be non-blank and unique.'
  };
  const tr = key => typeof window.t === 'function' ? window.t(labels[key]) : labels[key];
  const display = value => value == null ? '' : typeof value === 'string' ? value : JSON.stringify(value) ?? '';
  let nextRowId = 1;
  const row = (key, originalKey, value) => ({ id: nextRowId++, key, originalKey, description: display(value), originalValue: value });

  function mount(card, type, q) {
    const criteria = q.criteria ?? q.levels;
    const yesNo = q.base === 'noul' || (criteria && !Array.isArray(criteria) && typeof criteria === 'object' &&
      Object.keys(criteria).length > 0 && Object.keys(criteria).every(key => key === 'true' || key === 'false'));
    if (!supported.has(type) || (type === 'act' && yesNo)) return null;
    const source = card.querySelector('[data-f="criteria"]');
    const sourceField = source?.closest('.qf');
    if (!source || !sourceField) return null;
    const host = document.createElement('section');
    host.className = 'choice-editor';
    host.setAttribute('aria-label', tr('options'));
    sourceField.insertAdjacentElement('afterend', host);
    sourceField.hidden = true;

    const original = q.criteria ?? q.levels ?? [];
    let arrayShape = Array.isArray(original);
    let rows = arrayShape
      ? original.map(value => ({ ...row(String(value), String(value), value), description: '' }))
      : original && typeof original === 'object'
        ? Object.entries(original).map(([key, value]) => row(key, key, value))
        : [];
    let keysShown = q.option_keys !== 'hide';
    let errors = [];
    let writingSource = false;
    let costOutcomeRows = [];
    let validOutcomes = rows.map(item => item.key);
    let readOnly = Boolean(card._choiceEditorReadOnly);

    const currentOutcomes = () => Array.isArray(card._costOutcomes) ? [...card._costOutcomes] : [];
    const remapCostColumns = () => {
      if (type !== 'act' || typeof window.readCostRows !== 'function') return;
      const oldOutcomes = currentOutcomes();
      if (!oldOutcomes.length) return;
      const byId = new Map(rows.map(item => [item.id, item]));
      const remapped = new Map();
      oldOutcomes.forEach((outcome, index) => {
        const identity = costOutcomeRows[index];
        const item = byId.get(identity);
        if (item) remapped.set(outcome, item.key);
      });
      const costRows = window.readCostRows(card);
      card._costRows = costRows.map(item => ({
        ...item,
        costs: Object.fromEntries(Object.entries(item.costs).map(([outcome, value]) => [remapped.get(outcome) ?? outcome, value]))
      }));
      card.querySelectorAll('.cost-cell').forEach(input => {
        const next = remapped.get(input.dataset.outcome);
        if (next !== undefined) input.dataset.outcome = next;
      });
    };
    const rememberCostRows = () => {
      updateErrors();
      if (errors.length) return;
      const outcomes = currentOutcomes();
      costOutcomeRows = outcomes.map((outcome, index) =>
        rows.find(item => item.key === outcome)?.id ?? rows[index]?.id ?? null);
    };
    const syncSource = (emit = true) => {
      source.value = rows.map(item => `${item.key}: ${item.description}`).join('\n');
      const keys = rows.map(item => item.key);
      const valid = keys.every(key => key.trim()) && new Set(keys).size === keys.length;
      if (emit && valid) {
        remapCostColumns();
        validOutcomes = keys;
        writingSource = true;
        source.dispatchEvent(new Event('input', { bubbles: true }));
        writingSource = false;
        rememberCostRows();
      }
    };
    const updateErrors = () => {
      const keys = rows.map(item => item.key);
      errors = [];
      if (keys.some(key => !key.trim()) || new Set(keys).size !== keys.length) errors.push(tr('invalidKey'));
      const box = host.querySelector('.choice-editor-errors');
      if (box) box.textContent = errors.join(' ');
      rows.forEach((item, index) => {
        const keyInput = host.querySelectorAll('.choice-editor-key')[index];
        const descriptionInput = host.querySelectorAll('.choice-editor-description')[index];
        if (keyInput) keyInput.setAttribute('aria-invalid', String(!keys[index].trim() || keys.filter(key => key === keys[index]).length > 1));
        if (descriptionInput) descriptionInput.removeAttribute('aria-invalid');
      });
    };
    const render = focusIndex => {
      host.innerHTML = `<div class="choice-editor-toolbar"><label><input class="choice-editor-show-keys" type="checkbox"${keysShown ? ' checked' : ''}> ${escapeHtml(tr('show'))}</label><button class="choice-editor-add" type="button">${escapeHtml(tr('add'))}</button></div><ol class="choice-editor-list">${rows.map((item, index) => `<li class="choice-editor-row" data-row="${index}"><label class="choice-editor-key-field"><span>${escapeHtml(tr('key'))}</span><input class="input choice-editor-key" type="text" value="${escapeHtml(item.key)}" aria-label="${escapeHtml(tr('key'))} ${index + 1}"></label><label class="choice-editor-description-field"><span>${escapeHtml(tr('description'))}</span><input class="input choice-editor-description" type="text" value="${escapeHtml(item.description)}" aria-label="${escapeHtml(tr('description'))} ${index + 1}"></label><button type="button" class="choice-editor-move" data-direction="-1" aria-label="${escapeHtml(tr('moveUp'))}"${index === 0 ? ' disabled' : ''}>↑</button><button type="button" class="choice-editor-move" data-direction="1" aria-label="${escapeHtml(tr('moveDown'))}"${index === rows.length - 1 ? ' disabled' : ''}>↓</button><button type="button" class="choice-editor-remove" aria-label="${escapeHtml(tr('remove'))}">×</button></li>`).join('')}</ol><div class="choice-editor-errors" role="alert" aria-live="polite"></div>`;
      if (readOnly) {
        host.classList.add('choice-editor-read-only');
        host.querySelectorAll('input,button').forEach(control => { control.disabled = true; });
      }
    };
    const syncFromSource = () => {
      const lines = source.value.split('\n').map(line => line.trim()).filter(Boolean);
      const keyed = lines.length > 0 && lines.every(line => /^([^\s:]{1,40}):\s*(.*)$/.test(line));
      const next = keyed
        ? lines.map(line => { const [, key, description] = line.match(/^([^\s:]{1,40}):\s*(.*)$/); return [key, description]; })
        : lines.map((description, index) => [String(index), description]);
      const prior = new Map(rows.map(item => [item.key, item]));
      arrayShape = !keyed;
      rows = next.map(([key, description], index) => {
        const sameKey = prior.get(key);
        const sameDescription = [...prior.values()].find(item => item.description === description);
        const old = sameDescription && sameDescription !== sameKey && sameKey?.description !== description
          ? sameDescription : sameKey ?? sameDescription ?? rows[index];
        return { id: old?.id ?? nextRowId++, key, originalKey: old?.originalKey ?? key, description,
          originalValue: old && old.description === description ? old.originalValue : undefined };
      });
      render();
      if (!errors.length) {
        remapCostColumns();
        validOutcomes = rows.map(item => item.key);
        if (type === 'act' && typeof window.renderCostMatrix === 'function') window.renderCostMatrix(card);
        rememberCostRows();
      }
    };
    host.addEventListener('input', event => {
      const rowEl = event.target.closest('[data-row]');
      if (!rowEl) return;
      const item = rows[Number(rowEl.dataset.row)];
      if (event.target.matches('.choice-editor-key')) item.key = event.target.value;
      if (event.target.matches('.choice-editor-description')) item.description = event.target.value;
      updateErrors(); syncSource();
    });
    host.addEventListener('change', event => {
      if (event.target.matches('.choice-editor-show-keys')) { keysShown = event.target.checked; syncSource(); }
    });
    host.addEventListener('click', event => {
      const rowEl = event.target.closest('[data-row]');
      if (event.target.closest('.choice-editor-add')) {
        rows.push({ id: nextRowId++, key: '', originalKey: '', description: '', originalValue: undefined });
        render(rows.length - 1); syncSource();
      } else if (event.target.closest('.choice-editor-remove') && rowEl) {
        rows.splice(Number(rowEl.dataset.row), 1); render(); syncSource();
      } else if (event.target.matches('.choice-editor-move') && rowEl) {
        const index = Number(rowEl.dataset.row), next = index + Number(event.target.dataset.direction);
        if (next >= 0 && next < rows.length) { [rows[index], rows[next]] = [rows[next], rows[index]]; render(); syncSource(); }
      }
    });
    source.addEventListener('input', () => { if (!writingSource) syncFromSource(); });
    render();
    rememberCostRows();
    syncSource(false);
    card._choiceEditor = {
      read() {
        updateErrors();
        const hasDescriptions = rows.some(item => item.description.length);
        const criteria = arrayShape && !hasDescriptions
          ? rows.map(item => item.key === item.originalKey ? item.originalValue : item.key)
          : Object.fromEntries(rows.map(item => [item.key,
            !arrayShape && item.description === display(item.originalValue) ? item.originalValue
              : item.description.length ? item.description : null]));
        return { criteria, option_keys: keysShown ? 'show' : 'hide' };
      },
      snapshot() { return { arrayShape, keysShown, rows: rows.map(item => ({ ...item })), costOutcomes: [...validOutcomes], costOutcomeRows: [...costOutcomeRows] }; },
      restore(state) {
        if (!state || !Array.isArray(state.rows)) return;
        arrayShape = Boolean(state.arrayShape);
        keysShown = Boolean(state.keysShown);
        rows = state.rows.map(item => ({ ...item }));
        validOutcomes = Array.isArray(state.costOutcomes) ? [...state.costOutcomes] : rows.map(item => item.key);
        costOutcomeRows = Array.isArray(state.costOutcomeRows) ? [...state.costOutcomeRows] : [];
        nextRowId = Math.max(nextRowId,...rows.map(item=>item.id+1));
        render(); syncSource();
      },
      syncFromSource,
      rememberCostRows,
      costOutcomes() { return [...validOutcomes]; },
      setReadOnly() {
        readOnly = true;
        host.classList.add('choice-editor-read-only');
        host.querySelectorAll('input,button').forEach(control => { control.disabled = true; });
      },
      errors() { updateErrors(); return [...errors]; },
      source
    };
    return card._choiceEditor;
  }
  window.ChoiceEditor = {
    mount,
    read(card) { return card._choiceEditor ? card._choiceEditor.read() : null; },
    errors(card) { return card._choiceEditor ? card._choiceEditor.errors() : []; },
    snapshot(card) { return card._choiceEditor?.snapshot() ?? null; },
    restore(card, state) { card._choiceEditor?.restore(state); },
    syncFromSource(card) { card._choiceEditor?.syncFromSource(); },
    costsRendered(card) { card._choiceEditor?.rememberCostRows(); },
    costOutcomes(card) { return card._choiceEditor ? card._choiceEditor.costOutcomes() : null; },
    setReadOnly(card) {
      card._choiceEditorReadOnly = true;
      card._choiceEditor?.setReadOnly();
    }
  };
})();
