/**
 * Nova Web Control Hub — Merged & Fixed JS (v3)
 * ──────────────────────────────────────────────
 * Combines js-core.js (chat/interaction) and js-panels.js (system panels).
 *
 * Fixes applied:
 *   1. Single API declaration
 *   2. All element IDs match the HTML exactly
 *   3. All querySelector class names match the HTML exactly
 *   4. Correct navigation logic (panels, right-panel, chat specifics)
 *   5. Proper chat with SSE streaming
 *   6. Web Speech API for mic + speechSynthesis for voice
 *   7. Complete event listener wiring
 *   8. Unified init at bottom
 *
 * v3 changes (ChatGPT-like init behavior):
 *   - Default panel is "chat" (not "status")
 *   - Chat panel: shows welcomeScreen OR chatMessages (mutually exclusive),
 *     shows chatInputContainer, hides all .panel sections, hides rightPanel
 *   - Other panels: hide welcomeScreen, chatInputContainer, chatMessages,
 *     thinkingBubble; show matching panel-{name} section
 *   - welcomeScreen hidden on first message; chatMessages shown instead
 */

(function () {
  'use strict';

  // ═══════════════════════════════════════════════════════════════════════
  //  SINGLE API BASE — no duplicates
  // ═══════════════════════════════════════════════════════════════════════

  const API = window.location.origin;

  // ═══════════════════════════════════════════════════════════════════════
  //  STATE
  // ═══════════════════════════════════════════════════════════════════════

  let ws = null;
  let reconnectTimer = null;
  let requestCount = 0;
  let recognition = null;
  let isListening = false;
  let isSpeaking = false;
  let hasMessages = false; // tracks whether chat has any messages

  // ═══════════════════════════════════════════════════════════════════════
  //  DOM HELPERS
  // ═══════════════════════════════════════════════════════════════════════

  function $(id) { return document.getElementById(id); }

  function setText(id, val) {
    var el = $(id);
    if (el) el.textContent = val != null ? val : '—';
  }

  function setHTML(id, html) {
    var el = $(id);
    if (el) el.innerHTML = html;
  }

  async function apiFetch(path, opts) {
    opts = opts || {};
    requestCount++;
    var url = API + path;
    var res = await fetch(url, {
      headers: Object.assign({ 'Content-Type': 'application/json' }, opts.headers || {}),
      method: opts.method || 'GET',
      body: opts.body || undefined,
    });
    if (!res.ok) {
      var body = await res.text().catch(function () { return ''; });
      throw new Error('API ' + res.status + ': ' + body);
    }
    var ct = res.headers.get('content-type') || '';
    return ct.includes('application/json') ? res.json() : res.text();
  }

  function statusDot(ok) {
    return ok
      ? '<span class="status-dot online"></span>'
      : '<span class="status-dot offline"></span>';
  }

  function badge(text, cls) {
    return '<span class="badge ' + (cls || '') + '">' + text + '</span>';
  }

  function escapeHtml(s) {
    var d = document.createElement('div');
    d.appendChild(document.createTextNode(s));
    return d.innerHTML;
  }

  function scrollToBottom() {
    var el = $('chatMessages');
    if (el) el.scrollTop = el.scrollHeight;
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  1. NAVIGATION — matches HTML class names exactly
  // ═══════════════════════════════════════════════════════════════════════

  /**
   * switchPanel — activate a panel by name.
   *
   * v3 behaviour:
   *   "chat" panel:
   *     - Hide ALL .panel sections (chat lives outside them)
   *     - Show chatInputContainer
   *     - Show welcomeScreen if no messages yet, otherwise show chatMessages
   *     - Hide rightPanel (clean ChatGPT look)
   *
   *   Any other panel:
   *     - Hide welcomeScreen, chatInputContainer, chatMessages, thinkingBubble
   *     - Hide rightPanel
   *     - Show matching panel-{name} section
   */
  function switchPanel(target) {
    var panelName;

    if (typeof target === 'string') {
      panelName = target;
    } else if (target && target.dataset) {
      panelName = target.dataset.panel;
    }
    if (!panelName) return;

    // --- Deactivate all nav-items ---
    var navItems = document.querySelectorAll('.nav-item');
    navItems.forEach(function (item) {
      item.classList.remove('active');
    });

    // --- Hide all panels ---
    var panels = document.querySelectorAll('.panel');
    panels.forEach(function (p) {
      p.classList.remove('active');
      p.style.display = 'none';
    });

    // --- Activate the clicked nav-item ---
    var activeNav = document.querySelector('.nav-item[data-panel="' + panelName + '"]');
    if (activeNav) activeNav.classList.add('active');

    // --- Grab chat-specific elements ---
    var welcomeScreen = $('welcomeScreen');
    var chatInputContainer = $('chatInputContainer');
    var chatMessages = $('chatMessages');
    var thinkingBubble = $('thinkingBubble');
    var rightPanel = $('rightPanel');

    if (panelName === 'chat') {
      // ── Chat panel: no .panel section, uses its own elements ──

      // Show input bar
      if (chatInputContainer) chatInputContainer.style.display = '';

      // Welcome vs messages — mutually exclusive
      if (hasMessages) {
        if (welcomeScreen) {
          welcomeScreen.style.display = 'none';
          welcomeScreen.hidden = true;
        }
        if (chatMessages) chatMessages.style.display = '';
      } else {
        if (welcomeScreen) {
          welcomeScreen.style.display = '';
          welcomeScreen.hidden = false;
        }
        if (chatMessages) chatMessages.style.display = 'none';
      }

      // Keep it clean — no right panel by default (ChatGPT style)
      if (rightPanel) rightPanel.classList.remove('visible');

    } else {
      // ── Non-chat panels ──

      // Hide all chat-related elements
      if (welcomeScreen) {
        welcomeScreen.style.display = 'none';
        welcomeScreen.hidden = true;
      }
      if (chatInputContainer) chatInputContainer.style.display = 'none';
      if (chatMessages) chatMessages.style.display = 'none';
      if (thinkingBubble) thinkingBubble.setAttribute('hidden', '');

      // Hide right panel for non-chat panels too
      if (rightPanel) rightPanel.classList.remove('visible');

      // Show the matching panel section (id = "panel-{name}")
      var activePanel = $('panel-' + panelName);
      if (activePanel) {
        activePanel.classList.add('active');
        activePanel.style.display = '';
      }
    }
  }

  /**
   * toggleCategory — collapse/expand a sidebar nav category.
   *
   * HTML contract:
   *   • category wrapper has class "nav-category" with data-category attribute
   *   • clickable header has class "nav-category-toggle"
   *   • arrow icon has class "category-arrow"
   */
  function toggleCategory(toggle) {
    if (!toggle) return;
    var category = toggle.closest('.nav-category');
    if (!category) return;

    category.classList.toggle('collapsed');

    // Toggle visibility of child items within the category
    var items = category.querySelectorAll('.nav-item');
    items.forEach(function (item) {
      if (category.classList.contains('collapsed')) {
        item.style.display = 'none';
      } else {
        item.style.display = '';
      }
    });

    // Rotate the arrow icon
    var arrow = toggle.querySelector('.category-arrow');
    if (arrow) {
      arrow.classList.toggle('rotated');
    }
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  2. CHAT — message rendering, SSE streaming
  // ═══════════════════════════════════════════════════════════════════════

  /**
   * appendChat — add a message bubble to the chat panel.
   *
   * HTML contract for message bubbles:
   *   div.msg.user | div.msg.assistant
   *     div.msg-avatar  (assistant only)
   *     div.msg-content
   *       span.msg-label (assistant only, "Nova")
   *       span.msg-body  (the actual text)
   */
  function appendChat(role, content) {
    var chatMessages = $('chatMessages');
    if (!chatMessages) return null;

    // Mark that we have messages — switch from welcome to chat view
    if (!hasMessages) {
      hasMessages = true;
      var ws = $('welcomeScreen');
      if (ws) {
        ws.style.display = 'none';
        ws.hidden = true;
      }
      chatMessages.style.display = '';
    }

    var wrapper = document.createElement('div');
    wrapper.className = 'msg ' + role;

    if (role === 'assistant') {
      var avatar = document.createElement('div');
      avatar.className = 'msg-avatar';
      avatar.textContent = '✦';
      wrapper.appendChild(avatar);
    }

    var msgContent = document.createElement('div');
    msgContent.className = 'msg-content';

    if (role === 'assistant') {
      var label = document.createElement('span');
      label.className = 'msg-label';
      label.textContent = 'Nova';
      msgContent.appendChild(label);
    }

    var body = document.createElement('span');
    body.className = 'msg-body';

    if (role === 'user') {
      body.textContent = content;
    } else {
      body.innerHTML = formatMessage(content);
    }

    msgContent.appendChild(body);
    wrapper.appendChild(msgContent);
    chatMessages.appendChild(wrapper);
    scrollToBottom();

    return body; // return body span for streaming updates
  }

  /**
   * Minimal markdown-ish formatting.
   */
  function formatMessage(text) {
    if (!text) return '';
    var safe = text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');

    // Code blocks: ```...```
    safe = safe.replace(/```(\w*)\n?([\s\S]*?)```/g, function (_m, _lang, code) {
      return '<pre><code>' + code.trim() + '</code></pre>';
    });
    // Inline code
    safe = safe.replace(/`([^`]+)`/g, '<code>$1</code>');
    // Bold
    safe = safe.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    // Italic
    safe = safe.replace(/\*(.+?)\*/g, '<em>$1</em>');
    // Newlines
    safe = safe.replace(/\n/g, '<br>');

    return safe;
  }

  function hideWelcome() {
    var ws = $('welcomeScreen');
    if (ws) {
      ws.style.display = 'none';
      ws.hidden = true;
    }
    // Also ensure chatMessages is visible
    var cm = $('chatMessages');
    if (cm) cm.style.display = '';
    hasMessages = true;
  }

  function isWelcomeVisible() {
    var ws = $('welcomeScreen');
    if (!ws) return false;
    return ws.style.display !== 'none' && !ws.hidden;
  }

  // ── Thinking bubble (uses hidden attribute per spec) ───────────────────

  function showThinking() {
    var tb = $('thinkingBubble');
    if (tb) {
      tb.removeAttribute('hidden');
      scrollToBottom();
    }
  }

  function hideThinking() {
    var tb = $('thinkingBubble');
    if (tb) {
      tb.setAttribute('hidden', '');
    }
  }

  // ── SSE parser ─────────────────────────────────────────────────────────

  function parseSSEChunk(chunk) {
    var events = [];
    var blocks = chunk.split(/\n\n/);

    for (var i = 0; i < blocks.length; i++) {
      var block = blocks[i];
      if (!block.trim()) continue;

      var eventType = 'token';
      var data = '';

      var lines = block.split('\n');
      for (var j = 0; j < lines.length; j++) {
        var line = lines[j];
        if (line.indexOf('event: ') === 0) {
          eventType = line.slice(7).trim();
        } else if (line.indexOf('data: ') === 0) {
          data += line.slice(6);
        }
      }

      if (data) {
        events.push({ type: eventType, data: data });
      }
    }
    return events;
  }

  /**
   * sendChat — POST to /api/chat, stream SSE response.
   */
  async function sendChat() {
    var chatInput = $('chatInput');
    if (!chatInput) return;

    var text = chatInput.value.trim();
    if (!text) return;

    // Hide welcome on first message
    hideWelcome();

    // Render user message
    appendChat('user', text);
    chatInput.value = '';
    chatInput.style.height = 'auto';

    // Show thinking bubble (remove hidden attr)
    showThinking();

    // Prepare assistant placeholder for streaming
    var assistantBody = appendChat('assistant', '');
    var accumulated = '';
    var firstToken = true;

    try {
      var response = await fetch(API + '/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text }),
      });

      if (!response.ok) {
        hideThinking();
        if (assistantBody) assistantBody.innerHTML = formatMessage('⚠️ Error: could not reach Nova backend.');
        scrollToBottom();
        return;
      }

      var reader = response.body.getReader();
      var decoder = new TextDecoder();
      var buffer = '';

      while (true) {
        var chunk = await reader.read();
        if (chunk.done) break;

        buffer += decoder.decode(chunk.value, { stream: true });

        // Split on double-newline for complete SSE blocks
        var parts = buffer.split('\n\n');
        buffer = parts.pop(); // keep incomplete tail

        for (var pi = 0; pi < parts.length; pi++) {
          var sseEvents = parseSSEChunk(parts[pi] + '\n\n');
          for (var ei = 0; ei < sseEvents.length; ei++) {
            var evt = sseEvents[ei];
            var payload;
            try { payload = JSON.parse(evt.data); } catch (_e) { payload = evt.data; }

            if (evt.type === 'token' || evt.type === 'message') {
              if (firstToken) { hideThinking(); firstToken = false; }
              var t = (typeof payload === 'object' && payload !== null)
                ? (payload.token || payload.content || payload.text || '')
                : String(payload);
              accumulated += t;
              if (assistantBody) assistantBody.innerHTML = formatMessage(accumulated);
              scrollToBottom();

            } else if (evt.type === 'done') {
              hideThinking();
              var final = (typeof payload === 'object' && payload !== null)
                ? (payload.content || payload.text || payload.message || '')
                : '';
              if (final && assistantBody) assistantBody.innerHTML = formatMessage(final);
              scrollToBottom();

            } else if (evt.type === 'error') {
              hideThinking();
              var errMsg = (typeof payload === 'object' && payload !== null)
                ? (payload.message || payload.error || 'Unknown error')
                : String(payload);
              if (assistantBody) assistantBody.innerHTML = formatMessage('⚠️ ' + errMsg);
              scrollToBottom();
            }
          }
        }
      }

      // Flush remaining buffer
      if (buffer.trim()) {
        var remaining = parseSSEChunk(buffer + '\n\n');
        for (var ri = 0; ri < remaining.length; ri++) {
          var revt = remaining[ri];
          var rpayload;
          try { rpayload = JSON.parse(revt.data); } catch (_e) { rpayload = revt.data; }
          if (revt.type === 'done' || revt.type === 'token') {
            if (firstToken) { hideThinking(); firstToken = false; }
            var rt = (typeof rpayload === 'object' && rpayload !== null)
              ? (rpayload.token || rpayload.content || rpayload.text || '')
              : String(rpayload);
            if (rt) {
              accumulated += rt;
              if (assistantBody) assistantBody.innerHTML = formatMessage(accumulated);
            }
          }
        }
      }

      hideThinking();
      scrollToBottom();

    } catch (err) {
      hideThinking();
      console.error('sendChat error:', err);
      if (assistantBody) assistantBody.innerHTML = formatMessage('⚠️ Connection error. Please try again.');
      scrollToBottom();
    }
  }

  /**
   * loadHistory — fetch /api/history and populate chat.
   */
  async function loadHistory() {
    try {
      var res = await fetch(API + '/api/history');
      if (!res.ok) return;
      var data = await res.json();
      var messages = data.messages || data.history || data || [];
      if (!Array.isArray(messages) || messages.length === 0) return;
      // History exists — hide welcome, show messages
      hideWelcome();
      for (var i = 0; i < messages.length; i++) {
        var msg = messages[i];
        var role = msg.role || (msg.is_user ? 'user' : 'assistant');
        var content = msg.content || msg.text || msg.message || '';
        appendChat(role, content);
      }
    } catch (err) {
      console.error('loadHistory error:', err);
    }
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  3. VOICE / MICROPHONE — Web Speech API
  // ═══════════════════════════════════════════════════════════════════════

  function toggleMic() {
    if (isListening) {
      stopListening();
      return;
    }

    var SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      alert('Voice input is not supported in your browser. Try Chrome or Edge.');
      return;
    }

    recognition = new SpeechRecognition();
    recognition.lang = 'en-US';
    recognition.interimResults = true;
    recognition.continuous = false;
    recognition.maxAlternatives = 1;

    recognition.onstart = function () {
      isListening = true;
      var micBtn = $('micBtn');
      if (micBtn) micBtn.classList.add('listening');
    };

    recognition.onresult = function (event) {
      var transcript = '';
      for (var i = event.resultIndex; i < event.results.length; i++) {
        transcript += event.results[i][0].transcript;
      }
      var chatInput = $('chatInput');
      if (chatInput) chatInput.value = transcript;
    };

    recognition.onerror = function (event) {
      console.error('Speech recognition error:', event.error);
      stopListening();
    };

    recognition.onend = function () {
      stopListening();
    };

    try {
      recognition.start();
    } catch (err) {
      console.error('Could not start speech recognition:', err);
      stopListening();
    }
  }

  function stopListening() {
    isListening = false;
    var micBtn = $('micBtn');
    if (micBtn) micBtn.classList.remove('listening');
    if (recognition) {
      try { recognition.stop(); } catch (_e) { /* ignore */ }
      recognition = null;
    }
  }

  function toggleVoice() {
    var synth = window.speechSynthesis;
    if (!synth) {
      alert('Text-to-speech is not supported in your browser.');
      return;
    }

    if (isSpeaking) {
      synth.cancel();
      isSpeaking = false;
      var vb = $('voiceBtn');
      if (vb) vb.classList.remove('speaking');
      return;
    }

    // Find last assistant message body
    var chatMessages = $('chatMessages');
    var assistantBodies = chatMessages
      ? chatMessages.querySelectorAll('.msg.assistant .msg-body')
      : [];

    if (assistantBodies.length === 0) return;

    var lastBody = assistantBodies[assistantBodies.length - 1];
    var text = lastBody.textContent || lastBody.innerText || '';
    if (!text.trim()) return;

    var utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = 1.0;
    utterance.pitch = 1.0;

    var voiceBtn = $('voiceBtn');

    utterance.onstart = function () {
      isSpeaking = true;
      if (voiceBtn) voiceBtn.classList.add('speaking');
    };
    utterance.onend = function () {
      isSpeaking = false;
      if (voiceBtn) voiceBtn.classList.remove('speaking');
    };
    utterance.onerror = function () {
      isSpeaking = false;
      if (voiceBtn) voiceBtn.classList.remove('speaking');
    };

    synth.speak(utterance);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  4. THEME
  // ═══════════════════════════════════════════════════════════════════════

  function toggleTheme() {
    var body = document.body;
    if (body.classList.contains('dark-theme')) {
      body.classList.remove('dark-theme');
      body.classList.add('light-theme');
      localStorage.setItem('nova-theme', 'light');
    } else {
      body.classList.remove('light-theme');
      body.classList.add('dark-theme');
      localStorage.setItem('nova-theme', 'dark');
    }
  }

  function restoreTheme() {
    var saved = localStorage.getItem('nova-theme');
    if (saved === 'light') {
      document.body.classList.remove('dark-theme');
      document.body.classList.add('light-theme');
    } else {
      document.body.classList.remove('light-theme');
      document.body.classList.add('dark-theme');
    }
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  5. ACTION BUTTONS (welcome-card and data-action)
  // ═══════════════════════════════════════════════════════════════════════

  function handleAction(type) {
    // Switch to chat first
    switchPanel('chat');

    var prefills = {
      'create-image': 'Create an image of ',
      'write':        'Write ',
      'search':       'Search for ',
      'projects':     null,
    };

    if (type === 'projects') {
      switchPanel('projects');
      return;
    }

    var prefill = prefills[type];
    if (prefill) {
      hideWelcome();
      var chatInput = $('chatInput');
      if (chatInput) {
        chatInput.value = prefill;
        chatInput.focus();
        chatInput.setSelectionRange(prefill.length, prefill.length);
      }
    }
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  6. WEBSOCKET
  // ═══════════════════════════════════════════════════════════════════════

  function connectWS() {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;

    var proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(proto + '://' + location.host + '/ws');

    ws.onopen = function () {
      console.log('[WS] connected');
      setHeaderStatus('Connected', 'green');
      if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    };

    ws.onmessage = function (ev) {
      try {
        var msg = JSON.parse(ev.data);
        handleWSMessage(msg);
      } catch (e) {
        console.warn('[WS] non-JSON message:', ev.data);
      }
    };

    ws.onerror = function (err) {
      console.error('[WS] error:', err);
    };

    ws.onclose = function () {
      console.log('[WS] closed — reconnecting in 3s');
      setHeaderStatus('Disconnected', 'red');
      ws = null;
      if (!reconnectTimer) {
        reconnectTimer = setTimeout(function () { reconnectTimer = null; connectWS(); }, 3000);
      }
    };
  }

  function handleWSMessage(msg) {
    switch (msg.type) {
      case 'status':
        updateStatusFromData(msg.data || msg);
        break;
      case 'log':
        appendLog(msg.data || msg.message || msg);
        break;
      case 'chat':
        // Could be handled here for push-based chat
        break;
      case 'vision':
        if (msg.data) {
          setHTML('visionResult', '<pre class="vision-output">' + escapeHtml(
            typeof msg.data === 'string' ? msg.data : JSON.stringify(msg.data, null, 2)
          ) + '</pre>');
        }
        break;
      case 'config':
        if (msg.data) populateConfigForm(msg.data);
        break;
      case 'agent_switch':
        loadAgents();
        break;
      case 'provider_model':
        if (msg.data) setText('modelName', msg.data.model || msg.data.name || '');
        break;
      case 'settings':
        loadSettingsApiKey();
        break;
      default:
        console.log('[WS] unhandled type:', msg.type, msg);
    }
  }

  function setHeaderStatus(label, color) {
    var dot = document.querySelector('.chat-status-dot, #headerStatusDot');
    var txt = document.querySelector('.chat-status-text, #headerStatusText');
    if (dot) {
      dot.style.background = color;
      dot.className = dot.className.replace(/\bonline\b|\boffline\b|\bwarning\b/g, '');
      if (color === 'green') dot.classList.add('online');
      else if (color === 'red') dot.classList.add('offline');
      else dot.classList.add('warning');
    }
    if (txt) txt.textContent = label;
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  7. STATUS PANEL
  // ═══════════════════════════════════════════════════════════════════════

  async function refreshStatus() {
    try {
      var d = await apiFetch('/api/status');
      updateStatusFromData(d);
    } catch (e) {
      console.error('[Status] refresh failed:', e);
      setText('sPipeline', 'Error');
    }
  }

  function updateStatusFromData(d) {
    if (!d) return;

    // Pipeline
    setText('sPipeline', d.pipeline || d.pipeline_status || '—');
    var pipeEl = $('sPipeline');
    if (pipeEl) {
      pipeEl.className = pipeEl.className.replace(/\bstatus-\w+/g, '');
      var running = (d.pipeline === 'running' || d.pipeline_status === 'running');
      pipeEl.classList.add(running ? 'status-online' : 'status-offline');
    }

    setText('sOllama', d.ollama || d.ollama_status || '—');
    setText('sChatModel', d.chat_model || d.model || '—');
    setText('sVision', d.vision || d.vision_status || '—');
    setText('sLLMMode', d.llm_mode || d.mode || '—');

    // Uptime
    if (d.uptime != null) setText('sUptime', formatUptime(d.uptime));

    // Resources
    setText('sRAM', d.ram || d.memory || '—');
    setText('sGPU', d.gpu || d.gpu_usage || '—');

    // Models loaded
    if (d.models_loaded != null) {
      var ml = $('modelsLoaded');
      if (ml) {
        if (Array.isArray(d.models_loaded)) {
          ml.textContent = d.models_loaded.join(', ') || 'None';
        } else {
          ml.textContent = String(d.models_loaded);
        }
      }
    }

    // Usage metrics
    var metricsEl = $('usageMetrics');
    if (metricsEl && d.usage) {
      setText('metricRequests', d.usage.requests != null ? d.usage.requests : '—');
      setText('metricTokensIn', d.usage.tokens_in != null ? d.usage.tokens_in : '—');
      setText('metricTokensOut', d.usage.tokens_out != null ? d.usage.tokens_out : '—');
      setText('metricLatency', d.usage.latency != null ? d.usage.latency + 'ms' : '—');
    }

    // Right panel agent info
    setText('rpAgentName', d.active_agent || d.agent_name || '—');
    setText('rpAgentModel', d.chat_model || d.model || '—');
    setText('rpAgentStatus', d.pipeline === 'running' || d.pipeline_status === 'running' ? 'Online' : 'Offline');

    // Live context
    if (d.context) {
      setText('ctxMessageCount', d.context.messages != null ? d.context.messages : '—');
      setText('ctxTokenCount', d.context.tokens != null ? d.context.tokens : '—');
      setText('ctxSessionId', d.context.session_id || '—');
    }

    // Model name in right panel
    setText('modelName', d.chat_model || d.model || '—');
  }

  function formatUptime(seconds) {
    if (seconds == null || isNaN(seconds)) return '—';
    seconds = Math.floor(Number(seconds));
    if (seconds < 60) return seconds + 's';
    var mins = Math.floor(seconds / 60);
    var secs = seconds % 60;
    if (mins < 60) return mins + 'm ' + secs + 's';
    var hrs = Math.floor(mins / 60);
    var remMins = mins % 60;
    if (hrs < 24) return hrs + 'h ' + remMins + 'm';
    var days = Math.floor(hrs / 24);
    var remHrs = hrs % 24;
    return days + 'd ' + remHrs + 'h ' + remMins + 'm';
  }

  // ── Pipeline controls ──────────────────────────────────────────────────

  async function startPipeline() {
    try {
      await apiFetch('/api/start', { method: 'POST' });
      setText('sPipeline', 'Starting…');
      setTimeout(refreshStatus, 1500);
    } catch (e) {
      console.error('[Pipeline] start failed:', e);
      alert('Failed to start pipeline: ' + e.message);
    }
  }

  async function stopPipeline() {
    try {
      await apiFetch('/api/stop', { method: 'POST' });
      setText('sPipeline', 'Stopping…');
      setTimeout(refreshStatus, 1500);
    } catch (e) {
      console.error('[Pipeline] stop failed:', e);
      alert('Failed to stop pipeline: ' + e.message);
    }
  }

  async function restartPipeline() {
    try {
      await apiFetch('/api/restart', { method: 'POST' });
      setText('sPipeline', 'Restarting…');
      setTimeout(refreshStatus, 2000);
    } catch (e) {
      console.error('[Pipeline] restart failed:', e);
      alert('Failed to restart pipeline: ' + e.message);
    }
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  8. AGENTS PANEL
  // ═══════════════════════════════════════════════════════════════════════

  async function loadAgents() {
    try {
      var data = await apiFetch('/api/agents');
      var agents = Array.isArray(data) ? data : (data.agents || []);
      var active = agents.find(function (a) { return a.active || a.is_active; }) || agents[0];

      // Active agent card
      if (active) {
        setText('activeAgentName', active.name || active.id || '—');
        setText('activeAgentModel', active.model || '—');
        setText('activeAgentPrompt', active.system_prompt || active.prompt || '—');
      }

      // Agent list
      var listEl = $('agentList');
      if (listEl) {
        listEl.innerHTML = agents.map(function (a) {
          var isActive = a.active || a.is_active;
          return '<div class="agent-item ' + (isActive ? 'active' : '') + '" data-agent-id="' + escapeHtml(a.id || a.name) + '">'
            + '<div class="agent-item-header">'
            + '<span class="agent-name">' + escapeHtml(a.name || a.id) + '</span>'
            + (isActive ? badge('Active', 'badge-success') : '')
            + '</div>'
            + '<div class="agent-item-meta">'
            + '<span class="agent-model">' + escapeHtml(a.model || '—') + '</span>'
            + '</div>'
            + '</div>';
        }).join('');

        // Bind click handlers for agent switching
        listEl.querySelectorAll('.agent-item[data-agent-id]').forEach(function (item) {
          item.addEventListener('click', function () {
            switchAgent(this.dataset.agentId);
          });
        });
      }

      // Right panel
      setText('rpAgentName', active ? (active.name || active.id || '—') : '—');
      setText('rpAgentModel', active ? (active.model || '—') : '—');
      setText('rpAgentStatus', active ? 'Online' : 'Offline');

    } catch (e) {
      console.error('[Agents] load failed:', e);
    }
  }

  async function switchAgent(id) {
    try {
      await apiFetch('/api/agents/switch', {
        method: 'POST',
        body: JSON.stringify({ agent_id: id }),
      });
      await loadAgents();
      refreshStatus();
    } catch (e) {
      console.error('[Agents] switch failed:', e);
      alert('Failed to switch agent: ' + e.message);
    }
  }

  async function addAgent(e) {
    if (e) e.preventDefault();

    var name = ($('newAgentName') || {}).value;
    var model = ($('newAgentModel') || {}).value;
    var prompt = ($('newAgentPrompt') || {}).value;

    if (!name) { alert('Agent name is required.'); return; }

    try {
      await apiFetch('/api/agents', {
        method: 'POST',
        body: JSON.stringify({ name: name, model: model || undefined, system_prompt: prompt || undefined }),
      });
      if ($('newAgentName')) $('newAgentName').value = '';
      if ($('newAgentModel')) $('newAgentModel').value = '';
      if ($('newAgentPrompt')) $('newAgentPrompt').value = '';
      await loadAgents();
    } catch (e) {
      console.error('[Agents] add failed:', e);
      alert('Failed to add agent: ' + e.message);
    }
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  9. SKILLS PANEL
  // ═══════════════════════════════════════════════════════════════════════

  async function loadSkills() {
    try {
      var data = await apiFetch('/api/skills');
      var skills = Array.isArray(data) ? data : (data.skills || []);

      var total = skills.length;
      var activeCount = skills.filter(function (s) { return s.active !== false && s.enabled !== false; }).length;

      setText('skillsTotal', total);
      setText('skillsActive', activeCount);

      // Skills stats (summary element)
      var statsEl = $('skillsStats');
      if (statsEl) {
        statsEl.textContent = total + ' skills loaded, ' + activeCount + ' active';
      }

      // Skills list
      var listEl = $('skillsList');
      if (listEl) {
        listEl.innerHTML = skills.map(function (s) {
          var isActive = s.active !== false && s.enabled !== false;
          return '<div class="skill-item ' + (isActive ? '' : 'skill-disabled') + '">'
            + '<span class="skill-name">' + escapeHtml(s.name || s.id || '—') + '</span>'
            + (isActive ? badge('Active', 'badge-success') : badge('Inactive', 'badge-muted'))
            + (s.description ? '<p class="skill-desc">' + escapeHtml(s.description) + '</p>' : '')
            + '</div>';
        }).join('');
      }

      // Right panel skills overview
      var soEl = $('skillsOverview');
      if (soEl) {
        soEl.textContent = activeCount + '/' + total + ' active';
      }
    } catch (e) {
      console.error('[Skills] load failed:', e);
    }
  }

  async function reloadSkills() {
    try {
      await apiFetch('/api/skills/reload', { method: 'POST' });
      await loadSkills();
    } catch (e) {
      console.error('[Skills] reload failed:', e);
      alert('Failed to reload skills: ' + e.message);
    }
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  10. SETTINGS PANEL
  // ═══════════════════════════════════════════════════════════════════════

  async function loadConfig() {
    try {
      var cfg = await apiFetch('/api/config');
      populateConfigForm(cfg);
    } catch (e) {
      console.error('[Config] load failed:', e);
    }
  }

  function populateConfigForm(cfg) {
    if (!cfg) return;

    // Map config fields to element IDs
    var fieldMap = {
      settingDefaultModel: cfg.model || cfg.default_model || cfg.chat_model,
      settingVisionModel: cfg.vision_model || cfg.vision,
      settingTemperature: cfg.temperature,
      settingMaxTokens: cfg.max_tokens,
    };

    Object.keys(fieldMap).forEach(function (id) {
      var val = fieldMap[id];
      var el = $(id);
      if (!el || val == null) return;
      if (el.tagName === 'SELECT') {
        var opt = Array.from(el.options).find(function (o) { return o.value === String(val); });
        if (opt) opt.selected = true;
        else el.value = val;
      } else if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
        el.value = val;
      } else {
        el.textContent = val;
      }
    });

    // Info display
    setText('infoVersion', cfg.version || '—');
    setText('infoHost', cfg.host || '—');
    setText('infoPlatform', cfg.platform || '—');
    setText('infoNode', cfg.node_version || cfg.node || '—');
  }

  async function saveConfig(e) {
    if (e) e.preventDefault();

    var payload = {};

    var defaultModel = ($('settingDefaultModel') || {}).value;
    var visionModel = ($('settingVisionModel') || {}).value;
    var temperature = ($('settingTemperature') || {}).value;
    var maxTokens = ($('settingMaxTokens') || {}).value;

    if (defaultModel) payload.model = defaultModel;
    if (visionModel) payload.vision_model = visionModel;
    if (temperature) payload.temperature = parseFloat(temperature);
    if (maxTokens) payload.max_tokens = parseInt(maxTokens, 10);

    try {
      await apiFetch('/api/config', {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      await loadConfig();
      alert('Configuration saved.');
    } catch (e) {
      console.error('[Config] save failed:', e);
      alert('Failed to save configuration: ' + e.message);
    }
  }

  async function loadSettingsApiKey() {
    try {
      var data = await apiFetch('/api/settings');

      // Provider status
      var providerEl = $('providerStatus');
      if (providerEl && data.providers) {
        var providers = Array.isArray(data.providers)
          ? data.providers
          : Object.keys(data.providers).map(function (k) { return Object.assign({ name: k }, data.providers[k]); });
        providerEl.innerHTML = providers.map(function (p) {
          return '<div class="provider-card ' + (p.status === 'active' || p.available ? 'provider-active' : 'provider-inactive') + '">'
            + '<div class="provider-name">' + escapeHtml(p.name || p.id || '—') + '</div>'
            + '<div class="provider-status">' + escapeHtml(p.status || (p.available ? 'Active' : 'Inactive')) + '</div>'
            + '</div>';
        }).join('');
      }
    } catch (e) {
      console.error('[Settings] load failed:', e);
    }
  }

  async function saveApiKeys(e) {
    if (e) e.preventDefault();

    var openRouterKey = ($('settingOpenRouterKey') || {}).value;
    var openAIKey = ($('settingOpenAIKey') || {}).value;
    var anthropicKey = ($('settingAnthropicKey') || {}).value;

    var payload = {};
    if (openRouterKey) payload.openrouter_key = openRouterKey;
    if (openAIKey) payload.openai_key = openAIKey;
    if (anthropicKey) payload.anthropic_key = anthropicKey;

    try {
      await apiFetch('/api/settings/keys', {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      // Clear fields
      if ($('settingOpenRouterKey')) $('settingOpenRouterKey').value = '';
      if ($('settingOpenAIKey')) $('settingOpenAIKey').value = '';
      if ($('settingAnthropicKey')) $('settingAnthropicKey').value = '';
      await loadSettingsApiKey();
      alert('API keys saved.');
    } catch (e) {
      console.error('[Settings] save keys failed:', e);
      alert('Failed to save API keys: ' + e.message);
    }
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  11. VISION PANEL
  // ═══════════════════════════════════════════════════════════════════════

  async function analyzeVision() {
    var resultEl = $('visionResult');
    var promptEl = $('visionPrompt');
    var imageInput = $('visionImageInput');

    if (resultEl) resultEl.innerHTML = '<div class="loading-spinner"></div> Analyzing…';

    var formData = new FormData();
    if (promptEl && promptEl.value.trim()) {
      formData.append('prompt', promptEl.value.trim());
    }
    if (imageInput && imageInput.files && imageInput.files[0]) {
      formData.append('image', imageInput.files[0]);
    }

    try {
      requestCount++;
      var res = await fetch(API + '/api/vision/analyze', {
        method: 'POST',
        body: formData,
      });

      if (!res.ok) {
        var errBody = await res.text().catch(function () { return ''; });
        throw new Error('API ' + res.status + ': ' + errBody);
      }

      var data = await res.json();
      if (resultEl) {
        var content = data.analysis || data.result || data.description || JSON.stringify(data, null, 2);
        resultEl.innerHTML = '<pre class="vision-output">' + escapeHtml(content) + '</pre>';
      }
    } catch (e) {
      console.error('[Vision] analyze failed:', e);
      if (resultEl) resultEl.innerHTML = '<div class="error-text">Analysis failed: ' + escapeHtml(e.message) + '</div>';
    }
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  12. LOGS PANEL
  // ═══════════════════════════════════════════════════════════════════════

  async function refreshLogs() {
    try {
      var data = await apiFetch('/api/logs');
      var logs = Array.isArray(data) ? data : (data.logs || []);
      var logEl = $('logArea');
      if (logEl) {
        logEl.innerHTML = '';
        logs.forEach(function (entry) { appendLog(entry); });
        logEl.scrollTop = logEl.scrollHeight;
      }
    } catch (e) {
      console.error('[Logs] refresh failed:', e);
    }
  }

  function appendLog(entry) {
    var logEl = $('logArea');
    if (!logEl) return;

    var div = document.createElement('div');
    div.className = 'log-entry';

    if (typeof entry === 'string') {
      div.textContent = entry;
    } else {
      var ts = entry.timestamp || entry.time || '';
      var level = entry.level || entry.severity || 'info';
      var msg = entry.message || entry.msg || entry.text || JSON.stringify(entry);
      div.classList.add('log-' + level.toLowerCase());
      div.innerHTML = '<span class="log-ts">' + escapeHtml(ts) + '</span> '
        + '<span class="log-level">[' + escapeHtml(level.toUpperCase()) + ']</span> '
        + '<span class="log-msg">' + escapeHtml(msg) + '</span>';
    }

    logEl.appendChild(div);

    // Cap at 500 entries
    while (logEl.children.length > 500) {
      logEl.removeChild(logEl.firstChild);
    }

    logEl.scrollTop = logEl.scrollHeight;
  }

  function clearLogs() {
    var logEl = $('logArea');
    if (logEl) logEl.innerHTML = '';
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  13. ACCESSIBILITY
  // ═══════════════════════════════════════════════════════════════════════

  function handleAccessibilityToggle(type, checked) {
    var body = document.body;
    var className = 'a11y-' + type;

    if (checked) {
      body.classList.add(className);
    } else {
      body.classList.remove(className);
    }

    // Persist
    try {
      var prefs = JSON.parse(localStorage.getItem('nova_a11y') || '{}');
      prefs[type] = checked;
      localStorage.setItem('nova_a11y', JSON.stringify(prefs));
    } catch (_) { /* ignore */ }
  }

  function restoreA11y() {
    try {
      var prefs = JSON.parse(localStorage.getItem('nova_a11y') || '{}');
      Object.keys(prefs).forEach(function (type) {
        if (prefs[type]) {
          document.body.classList.add('a11y-' + type);
          // Sync checkbox state
          var idMap = {
            'high-contrast': 'toggleHighContrast',
            'large-text': 'toggleLargeText',
            'tts': 'toggleTTS',
          };
          var el = $(idMap[type]);
          if (el && el.type === 'checkbox') el.checked = true;
        }
      });
    } catch (_) { /* ignore */ }
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  14. RIGHT PANEL sections
  // ═══════════════════════════════════════════════════════════════════════

  function toggleRPSection(header) {
    if (!header) return;
    var section = header.closest('.rp-section') || header.parentElement;
    if (section) section.classList.toggle('collapsed');

    var icon = header.querySelector('.collapse-icon, .rp-toggle');
    if (icon) {
      icon.textContent = section.classList.contains('collapsed') ? '▸' : '▾';
    }
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  15. EVENT LISTENER WIRING
  // ═══════════════════════════════════════════════════════════════════════

  function wireEvents() {

    // ── Chat input ─────────────────────────────────────────────────────
    var sendBtnEl = $('sendBtn');
    var chatInputEl = $('chatInput');

    if (sendBtnEl) {
      sendBtnEl.addEventListener('click', function () { sendChat(); });
    }

    if (chatInputEl) {
      chatInputEl.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          sendChat();
        }
      });
      // Auto-resize textarea
      chatInputEl.addEventListener('input', function () {
        this.style.height = 'auto';
        this.style.height = Math.min(this.scrollHeight, 200) + 'px';
      });
    }

    // ── Mic & Voice ───────────────────────────────────────────────────
    var micBtnEl = $('micBtn');
    var voiceBtnEl = $('voiceBtn');

    if (micBtnEl) {
      micBtnEl.addEventListener('click', function () { toggleMic(); });
    }
    if (voiceBtnEl) {
      voiceBtnEl.addEventListener('click', function () { toggleVoice(); });
    }

    // ── Sidebar nav-items ─────────────────────────────────────────────
    document.querySelectorAll('.nav-item').forEach(function (item) {
      item.addEventListener('click', function (e) {
        e.preventDefault();
        switchPanel(this);
      });
    });

    // ── Sidebar nav-category-toggles ──────────────────────────────────
    document.querySelectorAll('.nav-category-toggle').forEach(function (toggle) {
      toggle.addEventListener('click', function (e) {
        e.stopPropagation();
        toggleCategory(this);
      });
    });

    // ── Welcome cards & action buttons ────────────────────────────────
    document.querySelectorAll('.welcome-card, .action-btn[data-action]').forEach(function (el) {
      el.addEventListener('click', function () {
        var action = this.dataset.action;
        if (action) handleAction(action);
      });
    });

    // ── Accessibility toggles ─────────────────────────────────────────
    var toggleHC = $('toggleHighContrast');
    var toggleLT = $('toggleLargeText');
    var toggleTTSEl = $('toggleTTS');

    if (toggleHC) {
      toggleHC.addEventListener('change', function () {
        handleAccessibilityToggle('high-contrast', this.checked);
      });
    }
    if (toggleLT) {
      toggleLT.addEventListener('change', function () {
        handleAccessibilityToggle('large-text', this.checked);
      });
    }
    if (toggleTTSEl) {
      toggleTTSEl.addEventListener('change', function () {
        handleAccessibilityToggle('tts', this.checked);
      });
    }

    // ── Form: Add Agent ───────────────────────────────────────────────
    var addAgentFormEl = $('addAgentForm');
    if (addAgentFormEl) {
      addAgentFormEl.addEventListener('submit', function (e) { addAgent(e); });
    }

    // ── Form: Config ──────────────────────────────────────────────────
    var configFormEl = $('configForm');
    if (configFormEl) {
      configFormEl.addEventListener('submit', function (e) { saveConfig(e); });
    }

    // ── Form: API Keys ────────────────────────────────────────────────
    var apiKeysFormEl = $('apiKeysForm');
    if (apiKeysFormEl) {
      apiKeysFormEl.addEventListener('submit', function (e) { saveApiKeys(e); });
    }

    // ── Pipeline buttons ──────────────────────────────────────────────
    var btnStart = $('btnStartPipeline');
    var btnStop = $('btnStopPipeline');
    var btnRestart = $('btnRestartPipeline');


    if (btnStart) btnStart.addEventListener('click', function () { startPipeline(); });
    if (btnStop) btnStop.addEventListener('click', function () { stopPipeline(); });
    if (btnRestart) btnRestart.addEventListener('click', function () { restartPipeline(); });

    // ── Skills reload ─────────────────────────────────────────────────
    var btnReloadSkills = $('btnReloadSkills');
    if (btnReloadSkills) {
      btnReloadSkills.addEventListener('click', function () { reloadSkills(); });
    }

    // ── Vision analyze ────────────────────────────────────────────────
    var btnAnalyze = $('btnAnalyzeVision');
    if (btnAnalyze) {
      btnAnalyze.addEventListener('click', function () { analyzeVision(); });
    }

    // ── Logs refresh / clear ──────────────────────────────────────────
    var btnRefreshLogs = $('btnRefreshLogs');
    var btnClearLogs = $('btnClearLogs');

    if (btnRefreshLogs) btnRefreshLogs.addEventListener('click', function () { refreshLogs(); });
    if (btnClearLogs) btnClearLogs.addEventListener('click', function () { clearLogs(); });

    // ── Theme toggle (if a button exists) ─────────────────────────────
    var themeBtn = document.querySelector('[data-action="toggle-theme"]');
    if (themeBtn) {
      themeBtn.addEventListener('click', function () { toggleTheme(); });
    }

    // ── Right panel section collapse toggles ──────────────────────────
    document.querySelectorAll('.rp-section-header, .rp-section-toggle').forEach(function (header) {
      header.addEventListener('click', function () { toggleRPSection(this); });
    });

    // ── Model selector (right panel) ──────────────────────────────────
    var modelSelectorEl = $('modelSelector');
    if (modelSelectorEl) {
      modelSelectorEl.addEventListener('change', function () {
        setText('modelName', this.value);
      });
    }
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  16. INIT
  // ═══════════════════════════════════════════════════════════════════════

  function init() {
    // Restore preferences
    restoreTheme();
    restoreA11y();

    // Hide thinking bubble initially
    hideThinking();

    // Wire all event listeners
    wireEvents();

    // Default active panel: chat (ChatGPT-like — opens to chat with welcome screen)
    switchPanel('chat');

    // Load chat history (will hide welcome + show messages if history exists)
    loadHistory();

    // Connect WebSocket + fetch initial data
    connectWS();
    refreshStatus();
    loadConfig();
    loadAgents();
    loadSkills();
    loadSettingsApiKey();

    // Periodic status refresh
    setInterval(refreshStatus, 10000);
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  EXPOSE GLOBALS — callable from inline handlers or external scripts
  // ═══════════════════════════════════════════════════════════════════════

  window.sendChat        = sendChat;
  window.appendChat      = appendChat;
  window.loadHistory     = loadHistory;
  window.showThinking    = showThinking;
  window.hideThinking    = hideThinking;
  window.toggleMic       = toggleMic;
  window.toggleVoice     = toggleVoice;
  window.toggleTheme     = toggleTheme;
  window.handleAction    = handleAction;
  window.switchPanel     = switchPanel;
  window.toggleCategory  = toggleCategory;
  window.refreshStatus   = refreshStatus;
  window.startPipeline   = startPipeline;
  window.stopPipeline    = stopPipeline;
  window.restartPipeline = restartPipeline;
  window.loadAgents      = loadAgents;
  window.switchAgent     = switchAgent;
  window.addAgent        = addAgent;
  window.loadSkills      = loadSkills;
  window.reloadSkills    = reloadSkills;
  window.loadConfig      = loadConfig;
  window.saveConfig      = saveConfig;
  window.saveApiKeys     = saveApiKeys;
  window.analyzeVision   = analyzeVision;
  window.refreshLogs     = refreshLogs;
  window.clearLogs       = clearLogs;
  window.connectWS       = connectWS;

  // ═══════════════════════════════════════════════════════════════════════
  //  BOOT
  // ═══════════════════════════════════════════════════════════════════════

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
