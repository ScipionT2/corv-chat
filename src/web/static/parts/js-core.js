/**
 * Nova Web Control Hub — Core Chat/Interaction JS
 * Handles: chat, streaming, thinking animation, voice/mic, theme, welcome screen, action buttons, sidebar nav.
 */

(function () {
  'use strict';

  const API = window.location.origin;

  // ─── DOM refs (resolved once on DOMContentLoaded) ───────────────────────────
  let chatMessages, chatInput, micBtn, voiceBtn, sendBtn, welcomeScreen, thinkingBubble;

  function resolveDOM() {
    chatMessages    = document.getElementById('chatMessages');
    chatInput       = document.getElementById('chatInput');
    micBtn          = document.getElementById('micBtn');
    voiceBtn        = document.getElementById('voiceBtn');
    sendBtn         = document.getElementById('sendBtn');
    welcomeScreen   = document.getElementById('welcomeScreen');
    thinkingBubble  = document.getElementById('thinkingBubble');
  }

  // ─── Helpers ────────────────────────────────────────────────────────────────
  function scrollToBottom() {
    if (chatMessages) {
      chatMessages.scrollTop = chatMessages.scrollHeight;
    }
  }

  function hideWelcome() {
    if (welcomeScreen) {
      welcomeScreen.classList.add('hidden');
      welcomeScreen.style.display = 'none';
    }
  }

  function isWelcomeVisible() {
    if (!welcomeScreen) return false;
    return !welcomeScreen.classList.contains('hidden') && welcomeScreen.style.display !== 'none';
  }

  // ─── 1. CHAT ───────────────────────────────────────────────────────────────

  /**
   * appendChat — add a message bubble to the chat panel.
   * @param {'user'|'assistant'} role
   * @param {string} content  — may contain markdown-ish text; we keep it simple (innerText for user, innerHTML for assistant)
   * @returns {HTMLElement} the content span inside the bubble (useful for streaming updates)
   */
  function appendChat(role, content) {
    if (!chatMessages) return null;

    const wrapper = document.createElement('div');
    wrapper.classList.add('chat-message', `chat-${role}`);

    if (role === 'assistant') {
      // Avatar + label
      const avatar = document.createElement('div');
      avatar.classList.add('chat-avatar');
      avatar.textContent = '✦';
      wrapper.appendChild(avatar);

      const label = document.createElement('span');
      label.classList.add('chat-label');
      label.textContent = 'Nova';
      wrapper.appendChild(label);
    }

    const bubble = document.createElement('div');
    bubble.classList.add('chat-bubble');

    const span = document.createElement('span');
    span.classList.add('chat-content');

    if (role === 'user') {
      span.textContent = content;
    } else {
      // Allow basic HTML for formatted assistant responses
      span.innerHTML = formatMessage(content);
    }

    bubble.appendChild(span);
    wrapper.appendChild(bubble);
    chatMessages.appendChild(wrapper);
    scrollToBottom();

    return span; // return content span for streaming updates
  }

  /**
   * Minimal formatting: convert newlines to <br>, escape HTML for safety,
   * and wrap code blocks.
   */
  function formatMessage(text) {
    if (!text) return '';
    // Escape HTML first
    let safe = text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');

    // Code blocks: ```...```
    safe = safe.replace(/```(\w*)\n?([\s\S]*?)```/g, function (_m, _lang, code) {
      return '<pre><code>' + code.trim() + '</code></pre>';
    });

    // Inline code: `...`
    safe = safe.replace(/`([^`]+)`/g, '<code>$1</code>');

    // Bold: **...**
    safe = safe.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

    // Italic: *...*
    safe = safe.replace(/\*(.+?)\*/g, '<em>$1</em>');

    // Newlines to <br>
    safe = safe.replace(/\n/g, '<br>');

    return safe;
  }

  /**
   * sendChat — send user message to /api/chat via SSE streaming.
   */
  async function sendChat() {
    if (!chatInput) return;

    const text = chatInput.value.trim();
    if (!text) return;

    // Hide welcome screen on first message
    hideWelcome();

    // Render user message
    appendChat('user', text);
    chatInput.value = '';
    chatInput.style.height = 'auto'; // reset textarea height

    // Show thinking animation
    showThinking();

    // Prepare assistant message placeholder for streaming
    const assistantSpan = appendChat('assistant', '');
    let accumulated = '';
    let firstToken = true;

    try {
      const response = await fetch(`${API}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text }),
      });

      if (!response.ok) {
        hideThinking();
        assistantSpan.innerHTML = formatMessage('⚠️ Error: could not reach Nova backend.');
        scrollToBottom();
        return;
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop(); // keep incomplete line in buffer

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            // Store event type for next data line
            buffer = line + '\n' + buffer;
            continue;
          }

          // Parse SSE data lines
          if (line.startsWith('data: ')) {
            const raw = line.slice(6);
            let eventType = 'token'; // default

            // Check if we stored an event line
            const eventMatch = buffer.match(/^event: (\w+)\n/);
            if (eventMatch) {
              eventType = eventMatch[1];
              buffer = buffer.replace(/^event: \w+\n/, '');
            }

            // Try to parse JSON, fall back to raw string
            let payload;
            try {
              payload = JSON.parse(raw);
            } catch (_e) {
              payload = raw;
            }

            handleSSEEvent(eventType, payload, assistantSpan, {
              accumulated: accumulated,
              firstToken: firstToken,
              setAccumulated: function (v) { accumulated = v; },
              setFirstToken: function (v) { firstToken = v; },
            });
          }
        }
      }

      // If stream ended without a 'done' event, finalize
      hideThinking();

    } catch (err) {
      hideThinking();
      console.error('sendChat error:', err);
      assistantSpan.innerHTML = formatMessage('⚠️ Connection error. Please try again.');
      scrollToBottom();
    }
  }

  /**
   * Process an individual SSE event.
   */
  function handleSSEEvent(eventType, payload, assistantSpan, state) {
    if (eventType === 'token' || eventType === 'message') {
      if (state.firstToken) {
        hideThinking();
        state.setFirstToken(false);
      }

      const tokenText = (typeof payload === 'object' && payload !== null)
        ? (payload.token || payload.content || payload.text || '')
        : String(payload);

      state.setAccumulated(state.accumulated + tokenText);
      assistantSpan.innerHTML = formatMessage(state.accumulated);
      scrollToBottom();

    } else if (eventType === 'done') {
      hideThinking();

      const finalText = (typeof payload === 'object' && payload !== null)
        ? (payload.content || payload.text || payload.message || '')
        : '';

      if (finalText) {
        assistantSpan.innerHTML = formatMessage(finalText);
      }
      scrollToBottom();

    } else if (eventType === 'error') {
      hideThinking();
      const errMsg = (typeof payload === 'object' && payload !== null)
        ? (payload.message || payload.error || 'Unknown error')
        : String(payload);
      assistantSpan.innerHTML = formatMessage('⚠️ ' + errMsg);
      scrollToBottom();
    }
  }

  /**
   * Re-parse raw SSE from the fetch stream. Handles the standard
   * "event: <type>\ndata: <payload>\n\n" format properly.
   */
  function parseSSEChunk(chunk) {
    const events = [];
    const blocks = chunk.split(/\n\n/);

    for (const block of blocks) {
      if (!block.trim()) continue;

      let eventType = 'token';
      let data = '';

      for (const line of block.split('\n')) {
        if (line.startsWith('event: ')) {
          eventType = line.slice(7).trim();
        } else if (line.startsWith('data: ')) {
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
   * Improved sendChat using proper SSE parsing.
   * Replaces the naive line-based parser above.
   */
  async function sendChatV2() {
    if (!chatInput) return;

    const text = chatInput.value.trim();
    if (!text) return;

    hideWelcome();
    appendChat('user', text);
    chatInput.value = '';
    chatInput.style.height = 'auto';

    showThinking();

    const assistantSpan = appendChat('assistant', '');
    let accumulated = '';
    let firstToken = true;

    try {
      const response = await fetch(`${API}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text }),
      });

      if (!response.ok) {
        hideThinking();
        assistantSpan.innerHTML = formatMessage('⚠️ Error: could not reach Nova backend.');
        scrollToBottom();
        return;
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // Split on double-newline to get complete SSE blocks
        const parts = buffer.split('\n\n');
        buffer = parts.pop(); // last part may be incomplete

        for (const part of parts) {
          const events = parseSSEChunk(part + '\n\n');
          for (const evt of events) {
            let payload;
            try {
              payload = JSON.parse(evt.data);
            } catch (_e) {
              payload = evt.data;
            }

            if (evt.type === 'token' || evt.type === 'message') {
              if (firstToken) {
                hideThinking();
                firstToken = false;
              }
              const t = (typeof payload === 'object' && payload !== null)
                ? (payload.token || payload.content || payload.text || '')
                : String(payload);
              accumulated += t;
              assistantSpan.innerHTML = formatMessage(accumulated);
              scrollToBottom();

            } else if (evt.type === 'done') {
              hideThinking();
              const final = (typeof payload === 'object' && payload !== null)
                ? (payload.content || payload.text || payload.message || '')
                : '';
              if (final) {
                assistantSpan.innerHTML = formatMessage(final);
              }
              scrollToBottom();

            } else if (evt.type === 'error') {
              hideThinking();
              const errMsg = (typeof payload === 'object' && payload !== null)
                ? (payload.message || payload.error || 'Unknown error')
                : String(payload);
              assistantSpan.innerHTML = formatMessage('⚠️ ' + errMsg);
              scrollToBottom();
            }
          }
        }
      }

      // Finalize: process any remaining buffer
      if (buffer.trim()) {
        const events = parseSSEChunk(buffer + '\n\n');
        for (const evt of events) {
          let payload;
          try { payload = JSON.parse(evt.data); } catch (_e) { payload = evt.data; }
          if (evt.type === 'done' || evt.type === 'token') {
            if (firstToken) { hideThinking(); firstToken = false; }
            const t = (typeof payload === 'object' && payload !== null)
              ? (payload.token || payload.content || payload.text || '')
              : String(payload);
            if (t) {
              accumulated += t;
              assistantSpan.innerHTML = formatMessage(accumulated);
            }
          }
        }
      }

      hideThinking();
      scrollToBottom();

    } catch (err) {
      hideThinking();
      console.error('sendChat error:', err);
      assistantSpan.innerHTML = formatMessage('⚠️ Connection error. Please try again.');
      scrollToBottom();
    }
  }

  /**
   * loadHistory — fetch /api/history and populate chat.
   */
  async function loadHistory() {
    try {
      const res = await fetch(`${API}/api/history`);
      if (!res.ok) return;

      const data = await res.json();
      const messages = data.messages || data.history || data || [];

      if (!Array.isArray(messages) || messages.length === 0) return;

      // Hide welcome if there's history
      hideWelcome();

      for (const msg of messages) {
        const role = msg.role || (msg.is_user ? 'user' : 'assistant');
        const content = msg.content || msg.text || msg.message || '';
        appendChat(role, content);
      }

    } catch (err) {
      console.error('loadHistory error:', err);
    }
  }

  // ─── 2. THINKING BUBBLE ────────────────────────────────────────────────────

  function showThinking() {
    if (thinkingBubble) {
      thinkingBubble.classList.add('visible');
      thinkingBubble.classList.remove('hidden');
      thinkingBubble.style.display = '';
      scrollToBottom();
    }
  }

  function hideThinking() {
    if (thinkingBubble) {
      thinkingBubble.classList.remove('visible');
      thinkingBubble.classList.add('hidden');
      thinkingBubble.style.display = 'none';
    }
  }

  // ─── 3. WELCOME SCREEN ─────────────────────────────────────────────────────
  // Welcome visibility is managed by hideWelcome() / isWelcomeVisible() above.
  // Action cards wire up via handleAction() below.

  // ─── 4. VOICE / MICROPHONE ─────────────────────────────────────────────────

  let recognition = null;
  let isListening = false;

  function toggleMic() {
    if (isListening) {
      stopListening();
      return;
    }

    // Browser support check
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      console.warn('SpeechRecognition not supported in this browser.');
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
      if (micBtn) micBtn.classList.add('listening');
    };

    recognition.onresult = function (event) {
      let transcript = '';
      for (let i = event.resultIndex; i < event.results.length; i++) {
        transcript += event.results[i][0].transcript;
      }
      if (chatInput) {
        chatInput.value = transcript;
      }
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
    if (micBtn) micBtn.classList.remove('listening');
    if (recognition) {
      try { recognition.stop(); } catch (_e) { /* ignore */ }
      recognition = null;
    }
  }

  let isSpeaking = false;

  function toggleVoice() {
    const synth = window.speechSynthesis;
    if (!synth) {
      alert('Text-to-speech is not supported in your browser.');
      return;
    }

    // If currently speaking, stop
    if (isSpeaking) {
      synth.cancel();
      isSpeaking = false;
      if (voiceBtn) voiceBtn.classList.remove('speaking');
      return;
    }

    // Find last assistant message
    const assistantMessages = chatMessages
      ? chatMessages.querySelectorAll('.chat-assistant .chat-content')
      : [];

    if (assistantMessages.length === 0) return;

    const lastMsg = assistantMessages[assistantMessages.length - 1];
    const text = lastMsg.textContent || lastMsg.innerText || '';
    if (!text.trim()) return;

    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = 1.0;
    utterance.pitch = 1.0;

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

  // ─── 5. THEME ──────────────────────────────────────────────────────────────

  function toggleTheme() {
    const body = document.body;
    const isDark = body.classList.contains('dark-theme');

    if (isDark) {
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
    const saved = localStorage.getItem('nova-theme');
    if (saved === 'light') {
      document.body.classList.remove('dark-theme');
      document.body.classList.add('light-theme');
    } else {
      // Default to dark
      document.body.classList.remove('light-theme');
      document.body.classList.add('dark-theme');
    }
  }

  // ─── 6. ACTION BUTTONS ─────────────────────────────────────────────────────

  function handleAction(type) {
    // Switch to the chat panel first
    const chatPanel = document.querySelector('[data-panel="chat"]');
    if (chatPanel) switchPanel(chatPanel);

    const prefills = {
      'create-image': 'Create an image of ',
      'write':        'Write ',
      'search':       'Search for ',
      'projects':     null, // special: switch to projects panel
    };

    if (type === 'projects') {
      const projPanel = document.querySelector('[data-panel="projects"]');
      if (projPanel) switchPanel(projPanel);
      return;
    }

    const prefill = prefills[type];
    if (prefill && chatInput) {
      hideWelcome();
      chatInput.value = prefill;
      chatInput.focus();
      // Place cursor at end
      chatInput.setSelectionRange(prefill.length, prefill.length);
    }
  }

  // ─── 7. SIDEBAR NAVIGATION ─────────────────────────────────────────────────

  /**
   * switchPanel — activate a panel by its nav element or panel name.
   * @param {HTMLElement|string} target — nav item element or panel name string
   */
  function switchPanel(target) {
    let panelName;

    if (typeof target === 'string') {
      panelName = target;
    } else if (target && target.dataset) {
      panelName = target.dataset.panel || target.getAttribute('data-panel');
    }

    if (!panelName) return;

    // Deactivate all nav items and panels
    const navItems = document.querySelectorAll('[data-panel]');
    navItems.forEach(function (item) {
      item.classList.remove('active');
    });

    const panels = document.querySelectorAll('.panel');
    panels.forEach(function (panel) {
      panel.classList.remove('active');
      panel.style.display = 'none';
    });

    // Activate target nav item
    const activeNav = document.querySelector('[data-panel="' + panelName + '"]');
    if (activeNav) activeNav.classList.add('active');

    // Activate target panel
    const activePanel = document.getElementById(panelName + '-panel')
      || document.getElementById(panelName + 'Panel')
      || document.querySelector('.panel[data-name="' + panelName + '"]')
      || document.getElementById(panelName);

    if (activePanel) {
      activePanel.classList.add('active');
      activePanel.style.display = '';
    }
  }

  /**
   * toggleCategory — collapse/expand a sidebar nav category.
   * @param {HTMLElement} header — the category header element
   */
  function toggleCategory(header) {
    if (!header) return;

    const parent = header.parentElement;
    if (!parent) return;

    parent.classList.toggle('collapsed');

    const list = parent.querySelector('.nav-category-list, .category-items, ul');
    if (list) {
      if (parent.classList.contains('collapsed')) {
        list.style.display = 'none';
      } else {
        list.style.display = '';
      }
    }

    // Toggle chevron icon if present
    const chevron = header.querySelector('.chevron, .toggle-icon, .arrow');
    if (chevron) {
      chevron.classList.toggle('rotated');
    }
  }

  // ─── INITIALIZATION ────────────────────────────────────────────────────────

  function init() {
    resolveDOM();
    restoreTheme();

    // Hide thinking bubble initially
    hideThinking();

    // Enter key sends message
    if (chatInput) {
      chatInput.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          sendChatV2();
        }
      });

      // Auto-resize textarea
      chatInput.addEventListener('input', function () {
        this.style.height = 'auto';
        this.style.height = Math.min(this.scrollHeight, 200) + 'px';
      });
    }

    // Send button
    if (sendBtn) {
      sendBtn.addEventListener('click', function () {
        sendChatV2();
      });
    }

    // Mic button
    if (micBtn) {
      micBtn.addEventListener('click', function () {
        toggleMic();
      });
    }

    // Voice button
    if (voiceBtn) {
      voiceBtn.addEventListener('click', function () {
        toggleVoice();
      });
    }

    // Action cards (welcome screen)
    const actionCards = document.querySelectorAll('[data-action]');
    actionCards.forEach(function (card) {
      card.addEventListener('click', function () {
        handleAction(this.dataset.action);
      });
    });

    // Sidebar nav items
    const navItems = document.querySelectorAll('[data-panel]');
    navItems.forEach(function (item) {
      item.addEventListener('click', function (e) {
        e.preventDefault();
        switchPanel(this);
      });
    });

    // Category headers
    const categoryHeaders = document.querySelectorAll('.nav-category-header, .category-header');
    categoryHeaders.forEach(function (header) {
      header.addEventListener('click', function () {
        toggleCategory(this);
      });
    });

    // Theme toggle button
    const themeBtn = document.getElementById('themeBtn') || document.querySelector('[data-action="toggle-theme"]');
    if (themeBtn) {
      themeBtn.addEventListener('click', function () {
        toggleTheme();
      });
    }

    // Load chat history
    loadHistory();
  }

  // ─── EXPOSE GLOBALS ────────────────────────────────────────────────────────
  // All functions callable from onclick handlers or external scripts.

  window.sendChat       = sendChatV2;
  window.appendChat     = appendChat;
  window.loadHistory    = loadHistory;
  window.showThinking   = showThinking;
  window.hideThinking   = hideThinking;
  window.toggleMic      = toggleMic;
  window.toggleVoice    = toggleVoice;
  window.toggleTheme    = toggleTheme;
  window.handleAction   = handleAction;
  window.switchPanel    = switchPanel;
  window.toggleCategory = toggleCategory;

  // Boot
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
