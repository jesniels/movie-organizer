'use strict';
// Startup: loaded last, after every feature script has registered its own DOMContentLoaded wiring.

document.addEventListener('DOMContentLoaded', () => {
  renderLocationFilters();   // render top-level rows before config arrives
  fetchConfig();
  pollScanStatus();   // immediate check on load; starts polling if scan is running
  pollTranscodeStatus();
  fetchLibrary();
  fetchStatus();
});
