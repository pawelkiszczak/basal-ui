'use strict';
// Appearance preferences: skin (brand palette) and theme (light / dark / system) are independent and kept in
// localStorage. Loaded without defer in <head>, so the document has its final skin and theme before the first paint.
const SKINS = ['green', 'orange'], THEMES = ['light', 'dark', 'system'];
function readPreference(key, allowed, fallback) {
  try { const value = localStorage.getItem(key); return allowed.includes(value) ? value : fallback; }
  catch { return fallback; }
}
function storePreference(key, value) { try { localStorage.setItem(key, value); } catch {} }
function syncPressed(attribute, value) {
  document.querySelectorAll(`[${attribute}]`).forEach(b => b.setAttribute('aria-pressed', String(b.getAttribute(attribute) === value)));
}
function applySkin(skin) {
  const value = SKINS.includes(skin) ? skin : 'green';  // an unknown or damaged preference falls back to green
  document.documentElement.dataset.skin = value;
  syncPressed('data-skin-choice', value);
  storePreference('basal-ui.skin', value);
}
function applyTheme(theme) {
  const value = THEMES.includes(theme) ? theme : 'system';
  const dark = value === 'dark' || (value === 'system' && matchMedia('(prefers-color-scheme: dark)').matches);
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  document.documentElement.classList.toggle('dark', dark);
  syncPressed('data-theme-choice', value);
  storePreference('basal-ui.theme', value);
}
document.documentElement.dataset.skin = readPreference('basal-ui.skin', SKINS, 'green');
applyTheme(readPreference('basal-ui.theme', THEMES, 'system'));
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
  if (readPreference('basal-ui.theme', THEMES, 'system') === 'system') applyTheme('system');
});
document.addEventListener('click', event => {
  const skin = event.target.closest('[data-skin-choice]'), theme = event.target.closest('[data-theme-choice]');
  if (skin) applySkin(skin.dataset.skinChoice);
  if (theme) applyTheme(theme.dataset.themeChoice);
});
document.addEventListener('DOMContentLoaded', () => {
  syncPressed('data-skin-choice', document.documentElement.dataset.skin);
  syncPressed('data-theme-choice', readPreference('basal-ui.theme', THEMES, 'system'));
});
