/* ═══════════════════════════════════════════════════════════════════════════
   Nova Web Control Hub — Panels / System JS
   Handles: WebSocket, Status, Agents, Skills, Settings, Vision, Logs,
            Providers, Accessibility, Right Panel, Init
   ═══════════════════════════════════════════════════════════════════════════ */

var API = API || window.location.origin;

// ── State ────────────────────────────────────────────────────────────────
let ws = null;
let reconnectTimer = null;
let requestCount = 0;

// ── Helpers ──────────────────────────────────────────────────────────────

function $(id) { return document.getElementById(id); }

function setText(id, val) {
  const el = $(id);
  if (el) el.textContent = val != null ? val : '—';
}

function setHTML(id, html) {
  const el = $(id);
  if (el) el.innerHTML = html;
}

async function apiFetch(path, opts = {}) {
  requestCount++;
  const url = API + path;
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    ...opts,
  });
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`API ${res.status}: ${body}`);
  }
  const ct = res.headers.get('content-type') || '';
  return ct.includes('application/json') ? res.json() : res.text();
}

function statusDot(ok) {
  return ok
    ? '<span class="status-dot online"></span>'
    : '<span class="status-dot offline"></span>';
}

function badge(text, cls) {
  return `<span class="badge ${cls || ''}">${text}</span>`;
}

// ═════════════════════════════════════════════════════════════════════════
//  1. WEBSOCKET
// ═════════════════════════════════════════════════════════════════════════

function connectWS() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;

  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws`);

  ws.onopen = () => {
    console.log('[WS] connected');
    setHeaderStatus('Connected', 'green');
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
  };

  ws.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      handleWSMessage(msg);
    } catch (e) {
      console.warn('[WS] non-JSON message:', ev.data);
    }
  };

  ws.onerror = (err) => {
    console.error('[WS] error:', err);
  };

  ws.onclose = () => {
    console.log('[WS] closed — reconnecting in 3s');
    setHeaderStatus('Disconnected', 'red');
    ws = null;
    if (!reconnectTimer) {
      reconnectTimer = setTimeout(() => { reconnectTimer = null; connectWS(); }, 3000);
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
      // Delegate to chat handler in js-core if available
      if (typeof handleChatWS === 'function') handleChatWS(msg);
      break;
    case 'vision':
      if (msg.data) {
        setHTML('visionResult', `<pre class="vision-output">${escapeHtml(
          typeof msg.data === 'string' ? msg.data : JSON.stringify(msg.data, null, 2)
        )}</pre>`);
      }
      break;
    case 'config':
      if (msg.data) populateConfigForm(msg.data);
      break;
    case 'agent_switch':
      loadAgents();
      break;
    case 'provider_model':
      if (msg.data) {
        setText('rpCurrentModel', msg.data.model || msg.data.name || '');
      }
      break;
    case 'settings':
      loadSettingsApiKey();
      break;
    default:
      console.log('[WS] unhandled type:', msg.type, msg);
  }
}

function setHeaderStatus(label, color) {
  const dot = document.querySelector('.chat-status-dot, #headerStatusDot');
  const txt = document.querySelector('.chat-status-text, #headerStatusText');
  if (dot) {
    dot.style.background = color;
    dot.className = dot.className.replace(/\bonline\b|\boffline\b|\bwarning\b/g, '');
    if (color === 'green') dot.classList.add('online');
    else if (color === 'red') dot.classList.add('offline');
    else if (color === 'orange' || color === 'yellow') dot.classList.add('warning');
  }
  if (txt) txt.textContent = label;
}

function escapeHtml(s) {
  const d = document.createElement('div');
  d.appendChild(document.createTextNode(s));
  return d.innerHTML;
}

// ═════════════════════════════════════════════════════════════════════════
//  2. STATUS PANEL
// ═════════════════════════════════════════════════════════════════════════

async function refreshStatus() {
  try {
    const d = await apiFetch('/api/status');
    updateStatusFromData(d);
  } catch (e) {
    console.error('[Status] refresh failed:', e);
    setText('sPipeline', 'Error');
  }
}

function updateStatusFromData(d) {
  if (!d) return;

  // Pipeline / core services
  setText('sPipeline', d.pipeline || d.pipeline_status || '—');
  const pipeEl = $('sPipeline');
  if (pipeEl) {
    pipeEl.className = pipeEl.className.replace(/\bstatus-\w+/g, '');
    const running = (d.pipeline === 'running' || d.pipeline_status === 'running');
    pipeEl.classList.add(running ? 'status-online' : 'status-offline');
  }

  setText('sOllama', d.ollama || d.ollama_status || '—');
  setText('sChatModel', d.chat_model || d.model || '—');
  setText('sVision', d.vision || d.vision_status || '—');
  setText('sLLMMode', d.llm_mode || d.mode || '—');

  // Uptime
  if (d.uptime != null) {
    setText('sUptime', formatUptime(d.uptime));
  }
  if (d.total_uptime != null) {
    setText('sTotalUptime', formatUptime(d.total_uptime));
  } else {
    setText('sTotalUptime', d.total_uptime_str || '—');
  }

  // Resources
  setText('sRAM', d.ram || d.memory || '—');
  setText('sGPU', d.gpu || d.gpu_usage || '—');

  // Models
  if (d.models_loaded != null) {
    const ml = $('modelsLoaded');
    if (ml) {
      if (Array.isArray(d.models_loaded)) {
        ml.textContent = d.models_loaded.join(', ') || 'None';
      } else {
        ml.textContent = String(d.models_loaded);
      }
    }
  }

  // Counters / monitors
  setText('sRequestsToday', d.requests_today != null ? d.requests_today : '—');
  setText('sScreenMonitor', d.screen_monitor || d.screen_monitor_status || '—');
  setText('sWakeWord', d.wake_word || d.wake_word_status || '—');

  // Right panel quick stats
  setText('rpCurrentModel', d.chat_model || d.model || '—');
  setText('rpScreenStatus', d.screen_monitor || d.screen_monitor_status || '—');
  setText('rpWakeStatus', d.wake_word || d.wake_word_status || '—');
  setText('rpRequestsToday', d.requests_today != null ? d.requests_today : '—');
  if (d.uptime != null) setText('rpUptime', formatUptime(d.uptime));
}

function formatUptime(seconds) {
  if (seconds == null || isNaN(seconds)) return '—';
  seconds = Math.floor(Number(seconds));
  if (seconds < 60) return `${seconds}s`;
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  if (mins < 60) return `${mins}m ${secs}s`;
  const hrs = Math.floor(mins / 60);
  const remMins = mins % 60;
  if (hrs < 24) return `${hrs}h ${remMins}m`;
  const days = Math.floor(hrs / 24);
  const remHrs = hrs % 24;
  return `${days}d ${remHrs}h ${remMins}m`;
}

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

// ═════════════════════════════════════════════════════════════════════════
//  3. AGENTS
// ═════════════════════════════════════════════════════════════════════════

async function loadAgents() {
  try {
    const data = await apiFetch('/api/agents');
    const agents = Array.isArray(data) ? data : (data.agents || []);
    const active = agents.find(a => a.active || a.is_active) || agents[0];

    // Active agent card
    if (active) {
      setText('activeAgentName', active.name || active.id || '—');
      setText('activeAgentModel', active.model || '—');
      setText('activeAgentPrompt', active.system_prompt || active.prompt || '—');
      setText('activeAgentHierarchy', active.hierarchy || active.role || '—');
      setText('chatBreadcrumb', active.name || 'Nova');
    }

    // Agent list
    const listEl = $('agentList');
    if (listEl) {
      listEl.innerHTML = agents.map(a => {
        const isActive = a.active || a.is_active;
        return `
          <div class="agent-item ${isActive ? 'active' : ''}" onclick="switchAgent('${a.id || a.name}')">
            <div class="agent-item-header">
              <span class="agent-name">${escapeHtml(a.name || a.id)}</span>
              ${isActive ? badge('Active', 'badge-success') : ''}
            </div>
            <div class="agent-item-meta">
              <span class="agent-model">${escapeHtml(a.model || '—')}</span>
              ${a.hierarchy ? `<span class="agent-hierarchy">${escapeHtml(a.hierarchy)}</span>` : ''}
            </div>
          </div>`;
      }).join('');
    }

    // Right panel agent profiles
    const rpEl = $('rpAgentProfiles');
    if (rpEl) {
      rpEl.innerHTML = agents.map(a => {
        const isActive = a.active || a.is_active;
        return `
          <div class="rp-agent ${isActive ? 'rp-agent-active' : ''}">
            <strong>${escapeHtml(a.name || a.id)}</strong>
            <span class="rp-agent-model">${escapeHtml(a.model || '')}</span>
          </div>`;
      }).join('');
    }
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

async function addAgent() {
  const name = ($('newAgentName') || {}).value;
  const model = ($('newAgentModel') || {}).value;
  const prompt = ($('newAgentPrompt') || {}).value;

  if (!name) { alert('Agent name is required.'); return; }

  try {
    await apiFetch('/api/agents', {
      method: 'POST',
      body: JSON.stringify({ name, model: model || undefined, system_prompt: prompt || undefined }),
    });
    // Clear form
    if ($('newAgentName')) $('newAgentName').value = '';
    if ($('newAgentModel')) $('newAgentModel').value = '';
    if ($('newAgentPrompt')) $('newAgentPrompt').value = '';
    await loadAgents();
  } catch (e) {
    console.error('[Agents] add failed:', e);
    alert('Failed to add agent: ' + e.message);
  }
}

// ═════════════════════════════════════════════════════════════════════════
//  4. SKILLS
// ═════════════════════════════════════════════════════════════════════════

async function loadSkills() {
  try {
    const data = await apiFetch('/api/skills');
    const skills = Array.isArray(data) ? data : (data.skills || []);

    const loaded = skills.length;
    const active = skills.filter(s => s.active !== false && s.enabled !== false).length;
    const inactive = loaded - active;

    setText('skillsLoadedCount', loaded);
    setText('skillsActiveCount', active);
    setText('skillsInactiveCount', inactive);

    // Right panel stats
    setText('rpSkillsCount', loaded);
    setText('rpSkillsActive', active);

    // Skills list
    const listEl = $('skillsList');
    if (listEl) {
      listEl.innerHTML = skills.map(s => {
        const isActive = s.active !== false && s.enabled !== false;
        return `
          <div class="skill-item ${isActive ? '' : 'skill-disabled'}">
            <span class="skill-name">${escapeHtml(s.name || s.id || '—')}</span>
            ${isActive ? badge('Active', 'badge-success') : badge('Inactive', 'badge-muted')}
            ${s.description ? `<p class="skill-desc">${escapeHtml(s.description)}</p>` : ''}
          </div>`;
      }).join('');
    }

    // Right panel skills list
    const rpList = $('rpSkillsList');
    if (rpList) {
      rpList.innerHTML = skills.slice(0, 20).map(s => {
        const isActive = s.active !== false && s.enabled !== false;
        return `<div class="rp-skill">${statusDot(isActive)} ${escapeHtml(s.name || s.id)}</div>`;
      }).join('');
      if (skills.length > 20) {
        rpList.innerHTML += `<div class="rp-skill rp-more">+${skills.length - 20} more</div>`;
      }
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

// ═════════════════════════════════════════════════════════════════════════
//  5. SETTINGS
// ═════════════════════════════════════════════════════════════════════════

async function loadConfig() {
  try {
    const cfg = await apiFetch('/api/config');
    populateConfigForm(cfg);
  } catch (e) {
    console.error('[Config] load failed:', e);
  }
}

function populateConfigForm(cfg) {
  if (!cfg) return;

  const map = {
    cfgVoice: cfg.voice || cfg.tts_voice,
    cfgModel: cfg.model || cfg.chat_model,
    cfgAccent: cfg.accent || cfg.tts_accent,
  };

  Object.entries(map).forEach(([id, val]) => {
    const el = $(id);
    if (!el || val == null) return;
    if (el.tagName === 'SELECT') {
      // Try to select matching option; fall back to setting value directly
      const opt = Array.from(el.options).find(o => o.value === val);
      if (opt) opt.selected = true;
      else el.value = val;
    } else if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
      el.value = val;
    } else {
      el.textContent = val;
    }
  });

  // Populate read-only config display
  const roEl = $('roConfig');
  if (roEl) {
    const display = Object.entries(cfg)
      .filter(([k]) => !k.startsWith('_'))
      .map(([k, v]) => `<div class="config-row"><span class="config-key">${escapeHtml(k)}</span><span class="config-val">${escapeHtml(String(v))}</span></div>`)
      .join('');
    roEl.innerHTML = display || '<em>No configuration loaded</em>';
  }
}

async function saveSettings() {
  const voice = ($('cfgVoice') || {}).value;
  const model = ($('cfgModel') || {}).value;
  const accent = ($('cfgAccent') || {}).value;

  const payload = {};
  if (voice) payload.voice = voice;
  if (model) payload.model = model;
  if (accent) payload.accent = accent;

  try {
    await apiFetch('/api/config', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    await loadConfig();
    alert('Settings saved.');
  } catch (e) {
    console.error('[Settings] save failed:', e);
    alert('Failed to save settings: ' + e.message);
  }
}

async function loadSettingsApiKey() {
  try {
    const data = await apiFetch('/api/settings');

    // Key status badge
    const badgeEl = $('keyStatusBadge');
    if (badgeEl) {
      const hasKey = data.has_key || data.openrouter_key_set || false;
      badgeEl.innerHTML = hasKey
        ? badge('Key Set', 'badge-success')
        : badge('No Key', 'badge-danger');
    }

    // Masked display
    if (data.masked_key || data.openrouter_key_masked) {
      setText('keyMaskedDisplay', data.masked_key || data.openrouter_key_masked);
    } else {
      setText('keyMaskedDisplay', data.has_key ? '••••••••' : 'Not configured');
    }

    // Provider status grid
    const gridEl = $('providerStatusGrid');
    if (gridEl && data.providers) {
      const providers = Array.isArray(data.providers) ? data.providers : Object.entries(data.providers).map(([k, v]) => ({ name: k, ...v }));
      gridEl.innerHTML = providers.map(p => `
        <div class="provider-card ${p.status === 'active' || p.available ? 'provider-active' : 'provider-inactive'}">
          <div class="provider-name">${escapeHtml(p.name || p.id || '—')}</div>
          <div class="provider-status">${p.status || (p.available ? 'Active' : 'Inactive')}</div>
          ${p.model ? `<div class="provider-model">${escapeHtml(p.model)}</div>` : ''}
        </div>`).join('');
    }
  } catch (e) {
    console.error('[Settings] key load failed:', e);
  }
}

async function saveApiKey() {
  const keyEl = $('settingsApiKey');
  const key = keyEl ? keyEl.value.trim() : '';
  if (!key) { alert('Please enter an API key.'); return; }

  try {
    await apiFetch('/api/settings/openrouter-key', {
      method: 'POST',
      body: JSON.stringify({ key }),
    });
    if (keyEl) keyEl.value = '';
    await loadSettingsApiKey();
    alert('API key saved.');
  } catch (e) {
    console.error('[Settings] save key failed:', e);
    alert('Failed to save API key: ' + e.message);
  }
}

async function removeApiKey() {
  if (!confirm('Remove the API key? This may disable external providers.')) return;

  try {
    await apiFetch('/api/settings/openrouter-key', {
      method: 'POST',
      body: JSON.stringify({ key: '' }),
    });
    await loadSettingsApiKey();
    alert('API key removed.');
  } catch (e) {
    console.error('[Settings] remove key failed:', e);
    alert('Failed to remove API key: ' + e.message);
  }
}

function toggleKeyVisibility() {
  const keyEl = $('settingsApiKey');
  const eyeEl = $('keyEyeBtn');
  if (!keyEl) return;

  if (keyEl.type === 'password') {
    keyEl.type = 'text';
    if (eyeEl) eyeEl.textContent = '🙈';
  } else {
    keyEl.type = 'password';
    if (eyeEl) eyeEl.textContent = '👁️';
  }
}

function mask_key(key) {
  if (!key || key.length < 8) return '••••••••';
  return key.slice(0, 4) + '•'.repeat(Math.max(key.length - 8, 4)) + key.slice(-4);
}

// ═════════════════════════════════════════════════════════════════════════
//  6. VISION
// ═════════════════════════════════════════════════════════════════════════

async function analyzeScreen() {
  const resultEl = $('visionResult');
  if (resultEl) resultEl.innerHTML = '<div class="loading-spinner"></div> Analyzing…';

  try {
    const data = await apiFetch('/api/vision/analyze');
    if (resultEl) {
      const content = data.analysis || data.result || data.description || JSON.stringify(data, null, 2);
      resultEl.innerHTML = `<pre class="vision-output">${escapeHtml(content)}</pre>`;
    }
  } catch (e) {
    console.error('[Vision] analyze failed:', e);
    if (resultEl) resultEl.innerHTML = `<div class="error-text">Analysis failed: ${escapeHtml(e.message)}</div>`;
  }
}

// ═════════════════════════════════════════════════════════════════════════
//  7. LOGS
// ═════════════════════════════════════════════════════════════════════════

async function refreshLogs() {
  try {
    const data = await apiFetch('/api/logs');
    const logs = Array.isArray(data) ? data : (data.logs || []);
    const logEl = $('logArea');
    if (logEl) {
      logEl.innerHTML = '';
      logs.forEach(entry => appendLog(entry));
      logEl.scrollTop = logEl.scrollHeight;
    }
  } catch (e) {
    console.error('[Logs] refresh failed:', e);
  }
}

function appendLog(entry) {
  const logEl = $('logArea');
  if (!logEl) return;

  const div = document.createElement('div');
  div.className = 'log-entry';

  if (typeof entry === 'string') {
    div.textContent = entry;
  } else {
    const ts = entry.timestamp || entry.time || '';
    const level = entry.level || entry.severity || 'info';
    const msg = entry.message || entry.msg || entry.text || JSON.stringify(entry);
    div.classList.add(`log-${level.toLowerCase()}`);
    div.innerHTML = `<span class="log-ts">${escapeHtml(ts)}</span> <span class="log-level">[${escapeHtml(level.toUpperCase())}]</span> <span class="log-msg">${escapeHtml(msg)}</span>`;
  }

  logEl.appendChild(div);

  // Keep max ~500 log entries
  while (logEl.children.length > 500) {
    logEl.removeChild(logEl.firstChild);
  }

  logEl.scrollTop = logEl.scrollHeight;
}

// ═════════════════════════════════════════════════════════════════════════
//  8. PROVIDERS
// ═════════════════════════════════════════════════════════════════════════

async function loadProviders() {
  try {
    const data = await apiFetch('/api/providers');
    const providers = Array.isArray(data) ? data : (data.providers || []);

    const gridEl = $('providerStatusGrid');
    if (gridEl) {
      gridEl.innerHTML = providers.map(p => `
        <div class="provider-card ${p.status === 'active' || p.available ? 'provider-active' : 'provider-inactive'}"
             onclick="selectModelProvider('${escapeHtml(p.name || p.id || '')}')">
          <div class="provider-name">${escapeHtml(p.name || p.id || '—')}</div>
          <div class="provider-status">${escapeHtml(p.status || (p.available ? 'Active' : 'Inactive'))}</div>
          ${p.models ? `<div class="provider-models">${p.models.length} model${p.models.length !== 1 ? 's' : ''}</div>` : ''}
        </div>`).join('');
    }
  } catch (e) {
    console.error('[Providers] load failed:', e);
  }
}

function selectModelProvider(provider) {
  const bodyEl = $('rpModelBody');
  if (bodyEl) {
    bodyEl.innerHTML = `<div class="loading-spinner"></div> Loading ${escapeHtml(provider)}…`;
  }

  apiFetch(`/api/providers/${encodeURIComponent(provider)}`)
    .then(data => {
      if (!bodyEl) return;
      const models = data.models || [];
      bodyEl.innerHTML = `
        <h4>${escapeHtml(data.name || provider)}</h4>
        ${data.description ? `<p>${escapeHtml(data.description)}</p>` : ''}
        <div class="provider-model-list">
          ${models.map(m => `
            <div class="provider-model-item">
              <span>${escapeHtml(typeof m === 'string' ? m : m.name || m.id)}</span>
              ${m.context_length ? `<span class="model-ctx">${m.context_length} ctx</span>` : ''}
            </div>`).join('')}
          ${models.length === 0 ? '<em>No models listed</em>' : ''}
        </div>`;
    })
    .catch(e => {
      console.error('[Providers] select failed:', e);
      if (bodyEl) bodyEl.innerHTML = `<div class="error-text">Failed to load provider details.</div>`;
    });
}

// ═════════════════════════════════════════════════════════════════════════
//  9. RIGHT PANEL
// ═════════════════════════════════════════════════════════════════════════

function toggleRPSection(header) {
  if (!header) return;
  const section = header.closest('.rp-section') || header.parentElement;
  if (section) section.classList.toggle('collapsed');

  const icon = header.querySelector('.collapse-icon, .rp-toggle');
  if (icon) {
    icon.textContent = section.classList.contains('collapsed') ? '▸' : '▾';
  }
}

// ═════════════════════════════════════════════════════════════════════════
//  10. ACCESSIBILITY
// ═════════════════════════════════════════════════════════════════════════

function toggleA11y(type) {
  const body = document.body;
  const btnMap = {
    'high-contrast': 'a11yContrast',
    'large-text': 'a11yLargeText',
    'tts': 'a11yTTS',
  };

  const className = `a11y-${type}`;
  const isActive = body.classList.toggle(className);

  const btnEl = $(btnMap[type]);
  if (btnEl) {
    btnEl.classList.toggle('active', isActive);
    btnEl.setAttribute('aria-pressed', String(isActive));
  }

  // Persist preference
  try {
    const prefs = JSON.parse(localStorage.getItem('nova_a11y') || '{}');
    prefs[type] = isActive;
    localStorage.setItem('nova_a11y', JSON.stringify(prefs));
  } catch (_) { /* ignore */ }
}

// Restore a11y preferences on load
(function restoreA11y() {
  try {
    const prefs = JSON.parse(localStorage.getItem('nova_a11y') || '{}');
    Object.entries(prefs).forEach(([type, active]) => {
      if (active) {
        document.body.classList.add(`a11y-${type}`);
        const btnMap = { 'high-contrast': 'a11yContrast', 'large-text': 'a11yLargeText', 'tts': 'a11yTTS' };
        const btn = $(btnMap[type]);
        if (btn) {
          btn.classList.add('active');
          btn.setAttribute('aria-pressed', 'true');
        }
      }
    });
  } catch (_) { /* ignore */ }
})();

// ═════════════════════════════════════════════════════════════════════════
//  11. INIT
// ═════════════════════════════════════════════════════════════════════════

connectWS();
refreshStatus();
loadConfig();
loadAgents();
loadSkills();
loadSettingsApiKey();

setInterval(refreshStatus, 10000);
