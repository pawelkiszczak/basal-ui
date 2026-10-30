// Runs last (index.html's order, tested): the first render and the initial loads, once every script has defined its
// functions and registered its listeners (showView dispatches backends:view, which backends.js listens for).
'use strict';
addQuestion();
showView(location.hash);
loadMemorySettings();
loadPresets();refreshUpdates();
 (async()=>{try{renderHardware(await api('/api/hardware'));}catch(e){$('#deviceName').textContent=t('Hardware unavailable: {message}',{message:e.message});}await Promise.all([loadSettings(),loadModels(),loadExamples(),refreshStatus(),loadServe()]);restorePlaygroundLanguageDraft();})();
