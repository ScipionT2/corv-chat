
/* ═══════════════════════════════════════════════════════════════
   Nova v5 — Navigation & Layout Patch
   Runs AFTER the preserved v3 JS to adapt to the new 3-column layout.
   Overrides switchPanel and adds new functionality.
   ═══════════════════════════════════════════════════════════════ */

(function() {
  'use strict';

  // Panel name mapping: new nav items → panel IDs
  var panelMap = {
    'chat':      'chat',
    'explore':   'explore',
    'memory':    'memory',
    'tasks':     'tasks',
    'knowledge': 'knowledge',
    'settings':  'settings',
    'status':    'status',
    'agents':    'agents',
    'skills':    'skills',
    'vision':    'vision',
    'logs':      'logs',
    'help':      'help',
    'scripts':   'scripts',
    'projects':  'projects',
  };

  var hasMessages = false;

  function $(id) { return document.getElementById(id); }

  // Override switchPanel for v5 layout
  window.switchPanel = function switchPanel(target) {
    var panelName;
    if (typeof target === 'string') {
      panelName = target;
    } else if (target && target.dataset) {
      panelName = target.dataset.panel;
    }
    if (!panelName) return;

    // Deactivate all nav items
    document.querySelectorAll('.nav-item').forEach(function(item) {
      item.classList.remove('active');
    });

    // Activate clicked nav item
    var activeNav = document.querySelector('.nav-item[data-panel="' + panelName + '"]');
    if (activeNav) activeNav.classList.add('active');

    // Hide all panels
    document.querySelectorAll('.panel').forEach(function(p) {
      p.classList.remove('active');
      p.style.display = 'none';
    });

    var welcomeScreen = $('welcomeScreen');
    var chatPanel = $('chatPanel');
    var chatInputContainer = $('chatInputContainer');
    var chatMessages = $('chatMessages');

    if (panelName === 'chat') {
      // Show chat view
      if (welcomeScreen) welcomeScreen.style.display = '';
      if (chatPanel) chatPanel.style.display = '';
      if (chatInputContainer) chatInputContainer.style.display = '';
      if (chatMessages) chatMessages.style.display = '';
    } else {
      // Hide chat, show panel
      if (welcomeScreen) welcomeScreen.style.display = 'none';
      if (chatPanel) chatPanel.style.display = 'none';

      var panel = $('panel-' + panelName);
      if (panel) {
        panel.classList.add('active');
        panel.style.display = '';
      }
    }
  };

  // Re-wire nav items for v5
  document.querySelectorAll('.nav-item').forEach(function(item) {
    item.addEventListener('click', function(e) {
      e.preventDefault();
      window.switchPanel(this);
    });
  });

  // Quick action cards
  document.querySelectorAll('.quick-card[data-action]').forEach(function(el) {
    el.addEventListener('click', function() {
      var action = this.dataset.action;
      var prefills = {
        'create-image': 'Create an image of ',
        'write': 'Help me write ',
        'search': 'Explain ',
        'code': 'Help me code ',
      };
      var prefill = prefills[action];
      if (prefill) {
        var welcomeMsg = $('welcomeMsg');
        if (welcomeMsg) welcomeMsg.style.display = 'none';
        var chatInput = $('chatInput');
        if (chatInput) {
          chatInput.value = prefill;
          chatInput.focus();
        }
      }
    });
  });

  // Clock update
  function updateClock() {
    var now = new Date();
    var h = now.getHours();
    var m = now.getMinutes();
    var ampm = h >= 12 ? 'PM' : 'AM';
    h = h % 12 || 12;
    var timeStr = h + ':' + (m < 10 ? '0' : '') + m + ' ' + ampm;
    var months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    var dateStr = months[now.getMonth()] + ' ' + now.getDate();

    var clockTime = $('clockTime');
    var clockDate = $('clockDate');
    if (clockTime) clockTime.textContent = timeStr;
    if (clockDate) clockDate.textContent = dateStr;
  }
  updateClock();
  setInterval(updateClock, 30000);

  // Initialize to chat view
  window.switchPanel('chat');

})();
