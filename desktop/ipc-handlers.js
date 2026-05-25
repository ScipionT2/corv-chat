/**
 * @module ipc-handlers
 * @description Desktop-only IPC handlers for Nova's agent, skills, memory,
 * scheduler, and system subsystems.
 *
 * Usage in main.js:
 *   const { registerDesktopIPC } = require('./ipc-handlers');
 *   registerDesktopIPC(ipcMain, mainWindow);
 */

'use strict';

const path = require('path');
const fs = require('fs');
const { dialog } = require('electron');

// ── Lazy module loaders ─────────────────────────────────────────────
// Each subsystem may not exist yet — wrap in try/catch with helpful fallbacks.

function tryRequire(modPath) {
  try {
    return require(modPath);
  } catch (err) {
    console.warn(`[Nova IPC] Module not loaded: ${modPath} — ${err.message}`);
    return null;
  }
}

// ── Agent registry ──────────────────────────────────────────────────
// Maps agentId → running BaseAgent instance
const runningAgents = new Map();

// Default agent definitions (used when agents/ modules aren't loaded)
const DEFAULT_AGENTS = [
  { id: 'simple',       name: 'Simple',       icon: '💬', type: 'on-demand',   description: 'Direct chat agent. Pure LLM conversation.' },
  { id: 'orchestrator', name: 'Orchestrator', icon: '🎯', type: 'on-demand',   description: 'Breaks tasks into subtasks and delegates.' },
  { id: 'research',     name: 'Research',     icon: '🔍', type: 'on-demand',   description: 'Deep research with web search and analysis.' },
  { id: 'code',         name: 'Code',         icon: '🧑‍💻', type: 'on-demand',   description: 'Writes, reviews, and refactors code.' },
  { id: 'react',        name: 'ReAct',        icon: '⚡', type: 'on-demand',   description: 'Reason + Act loop with tool use.' },
  { id: 'monitor',      name: 'Monitor',      icon: '📡', type: 'continuous',  description: 'Watches metrics and alerts on anomalies.' },
  { id: 'digest',       name: 'Digest',       icon: '📰', type: 'scheduled',   description: 'Compiles periodic summaries.' },
  { id: 'operative',    name: 'Operative',    icon: '🕵️', type: 'on-demand',   description: 'Stealth agent for sensitive local operations.' },
];

// Agent config overrides (stored in memory, persisted to settings)
const agentConfigs = new Map();

/**
 * Register all desktop-only IPC handlers.
 *
 * @param {Electron.IpcMain} ipcMain
 * @param {function(): Electron.BrowserWindow|null} getMainWindow - Getter for the main window reference
 */
function registerDesktopIPC(ipcMain, getMainWindow) {
  // ════════════════════════════════════════════════════════════════════
  // AGENTS
  // ════════════════════════════════════════════════════════════════════

  ipcMain.handle('agents:list', async () => {
    try {
      // Try to load agent definitions from agents/ directory
      const agentsDir = path.join(__dirname, 'agents');
      const indexPath = path.join(agentsDir, 'index.js');
      const agentModule = tryRequire(indexPath);
      if (agentModule && typeof agentModule.listAgents === 'function') {
        return await agentModule.listAgents();
      }
    } catch (_) {}
    return DEFAULT_AGENTS;
  });

  ipcMain.handle('agents:run', async (_event, agentId, input) => {
    try {
      const BaseAgent = tryRequire(path.join(__dirname, 'agents', 'base-agent.js'));
      if (!BaseAgent) {
        // Fallback: use Ollama directly via a simple agent wrapper
        return await fallbackAgentRun(agentId, input);
      }

      // Check for a specific agent class
      const specificPath = path.join(__dirname, 'agents', `${agentId}-agent.js`);
      let AgentClass = tryRequire(specificPath);
      if (!AgentClass) AgentClass = BaseAgent;

      // Build config from overrides
      const cfg = agentConfigs.get(agentId) || {};
      const agent = typeof AgentClass === 'function'
        ? (AgentClass.prototype ? new AgentClass(agentId, 'on-demand', cfg) : new BaseAgent(agentId, 'on-demand', cfg))
        : new BaseAgent(agentId, 'on-demand', cfg);

      runningAgents.set(agentId, agent);

      // Stream chunks to renderer if possible
      const win = typeof getMainWindow === 'function' ? getMainWindow() : null;
      if (win) {
        agent.on('chunk', (chunk) => {
          win.webContents.send(`agent:chunk:${agentId}`, chunk);
        });
        agent.on('step', (step) => {
          win.webContents.send(`agent:step:${agentId}`, step);
        });
      }

      const result = await agent.run(input);
      runningAgents.delete(agentId);
      return result;
    } catch (err) {
      runningAgents.delete(agentId);
      return { output: `Error: ${err.message}`, error: err.message, steps: [], metadata: { error: true } };
    }
  });

  ipcMain.handle('agents:stop', async (_event, agentId) => {
    const agent = runningAgents.get(agentId);
    if (agent) {
      agent.stop();
      runningAgents.delete(agentId);
      return { ok: true };
    }
    return { ok: false, error: 'Agent not running' };
  });

  ipcMain.handle('agents:configure', async (_event, agentId, config) => {
    agentConfigs.set(agentId, config);
    // Persist to settings file
    try {
      const { app } = require('electron');
      const cfgPath = path.join(app.getPath('userData'), 'agent-configs.json');
      const all = {};
      for (const [k, v] of agentConfigs) all[k] = v;
      fs.writeFileSync(cfgPath, JSON.stringify(all, null, 2));
    } catch (_) {}
    return { ok: true };
  });

  // ════════════════════════════════════════════════════════════════════
  // SKILLS
  // ════════════════════════════════════════════════════════════════════

  ipcMain.handle('skills:list', async () => {
    try {
      const skillsModule = tryRequire(path.join(__dirname, 'skills', 'index.js'));
      if (skillsModule && typeof skillsModule.listSkills === 'function') {
        return await skillsModule.listSkills();
      }
    } catch (_) {}

    // Fallback: scan skills/builtin directory
    const builtinDir = path.join(__dirname, 'skills', 'builtin');
    const skills = [];
    try {
      if (fs.existsSync(builtinDir)) {
        const entries = fs.readdirSync(builtinDir, { withFileTypes: true });
        for (const entry of entries) {
          if (entry.isDirectory()) {
            const manifestPath = path.join(builtinDir, entry.name, 'manifest.json');
            if (fs.existsSync(manifestPath)) {
              try {
                const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf-8'));
                skills.push({ ...manifest, builtin: true });
              } catch (_) {}
            } else {
              skills.push({
                id: entry.name,
                name: entry.name,
                description: `Built-in skill: ${entry.name}`,
                category: 'system',
                version: '1.0.0',
                builtin: true,
              });
            }
          }
        }
      }
    } catch (_) {}
    return skills;
  });

  ipcMain.handle('skills:install', async (_event, githubUrl) => {
    try {
      const skillsModule = tryRequire(path.join(__dirname, 'skills', 'index.js'));
      if (skillsModule && typeof skillsModule.installSkill === 'function') {
        return await skillsModule.installSkill(githubUrl);
      }

      // Fallback: clone the repo into skills/installed/<name>
      const { execSync } = require('child_process');
      const urlObj = new URL(githubUrl);
      const repoName = path.basename(urlObj.pathname, '.git');
      const installDir = path.join(__dirname, 'skills', 'installed');
      if (!fs.existsSync(installDir)) fs.mkdirSync(installDir, { recursive: true });

      const targetDir = path.join(installDir, repoName);
      if (fs.existsSync(targetDir)) {
        return { ok: false, error: `Skill "${repoName}" is already installed` };
      }

      execSync(`git clone --depth 1 "${githubUrl}" "${targetDir}"`, {
        timeout: 60000,
        stdio: 'pipe',
      });

      return { ok: true, name: repoName };
    } catch (err) {
      return { ok: false, error: err.message };
    }
  });

  ipcMain.handle('skills:uninstall', async (_event, skillId) => {
    try {
      const skillsModule = tryRequire(path.join(__dirname, 'skills', 'index.js'));
      if (skillsModule && typeof skillsModule.uninstallSkill === 'function') {
        return await skillsModule.uninstallSkill(skillId);
      }

      // Fallback: remove from skills/installed/<skillId>
      const installDir = path.join(__dirname, 'skills', 'installed', skillId);
      if (fs.existsSync(installDir)) {
        fs.rmSync(installDir, { recursive: true, force: true });
        return { ok: true };
      }
      return { ok: false, error: `Skill "${skillId}" not found` };
    } catch (err) {
      return { ok: false, error: err.message };
    }
  });

  // ════════════════════════════════════════════════════════════════════
  // MEMORY
  // ════════════════════════════════════════════════════════════════════

  ipcMain.handle('memory:search', async (_event, query, topK = 10) => {
    try {
      const memModule = tryRequire(path.join(__dirname, 'memory', 'index.js'));
      if (memModule && typeof memModule.search === 'function') {
        return await memModule.search(query, topK);
      }
    } catch (_) {}
    return [];
  });

  ipcMain.handle('memory:index-file', async (_event, filePath) => {
    try {
      const memModule = tryRequire(path.join(__dirname, 'memory', 'index.js'));
      if (memModule && typeof memModule.indexFile === 'function') {
        return await memModule.indexFile(filePath);
      }
      // Fallback: at least verify the file exists
      if (!fs.existsSync(filePath)) {
        return { ok: false, error: 'File not found' };
      }
      return { ok: false, error: 'Memory module not loaded yet. Index functionality coming soon.' };
    } catch (err) {
      return { ok: false, error: err.message };
    }
  });

  ipcMain.handle('memory:index-dir', async (_event, dirPath) => {
    try {
      const memModule = tryRequire(path.join(__dirname, 'memory', 'index.js'));
      if (memModule && typeof memModule.indexDir === 'function') {
        return await memModule.indexDir(dirPath);
      }
      if (!fs.existsSync(dirPath)) {
        return { ok: false, error: 'Directory not found' };
      }
      return { ok: false, error: 'Memory module not loaded yet. Index functionality coming soon.' };
    } catch (err) {
      return { ok: false, error: err.message };
    }
  });

  ipcMain.handle('memory:forget', async (_event, id) => {
    try {
      const memModule = tryRequire(path.join(__dirname, 'memory', 'index.js'));
      if (memModule && typeof memModule.forget === 'function') {
        return await memModule.forget(id);
      }
      return { ok: false, error: 'Memory module not loaded yet' };
    } catch (err) {
      return { ok: false, error: err.message };
    }
  });

  ipcMain.handle('memory:stats', async () => {
    try {
      const memModule = tryRequire(path.join(__dirname, 'memory', 'index.js'));
      if (memModule && typeof memModule.stats === 'function') {
        return await memModule.stats();
      }
    } catch (_) {}
    // Fallback: scan memory directory for basic stats
    const memDir = path.join(__dirname, 'memory');
    let totalFiles = 0;
    let totalSize = 0;
    try {
      if (fs.existsSync(memDir)) {
        const files = fs.readdirSync(memDir);
        for (const f of files) {
          try {
            const stat = fs.statSync(path.join(memDir, f));
            if (stat.isFile()) { totalFiles++; totalSize += stat.size; }
          } catch (_) {}
        }
      }
    } catch (_) {}
    return {
      totalMemories: totalFiles,
      diskUsage: formatBytes(totalSize),
      lastIndexed: null,
    };
  });

  ipcMain.handle('memory:remember', async (_event, text, metadata = {}) => {
    try {
      const memModule = tryRequire(path.join(__dirname, 'memory', 'index.js'));
      if (memModule && typeof memModule.remember === 'function') {
        return await memModule.remember(text, metadata);
      }

      // Fallback: write to a simple JSON-lines file
      const memDir = path.join(__dirname, 'memory');
      if (!fs.existsSync(memDir)) fs.mkdirSync(memDir, { recursive: true });
      const storePath = path.join(memDir, 'memories.jsonl');
      const entry = {
        id: `mem_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
        text,
        source: metadata.source || 'manual',
        timestamp: Date.now(),
      };
      fs.appendFileSync(storePath, JSON.stringify(entry) + '\n');
      return { ok: true, id: entry.id };
    } catch (err) {
      return { ok: false, error: err.message };
    }
  });

  // ════════════════════════════════════════════════════════════════════
  // SCHEDULER
  // ════════════════════════════════════════════════════════════════════

  ipcMain.handle('scheduler:list', async () => {
    try {
      const schedModule = tryRequire(path.join(__dirname, 'scheduler', 'index.js'));
      if (schedModule && typeof schedModule.list === 'function') {
        return await schedModule.list();
      }
    } catch (_) {}

    // Fallback: read from schedules.json
    const schedPath = path.join(__dirname, 'scheduler', 'schedules.json');
    try {
      if (fs.existsSync(schedPath)) {
        return JSON.parse(fs.readFileSync(schedPath, 'utf-8'));
      }
    } catch (_) {}
    return [];
  });

  ipcMain.handle('scheduler:add', async (_event, config) => {
    try {
      const schedModule = tryRequire(path.join(__dirname, 'scheduler', 'index.js'));
      if (schedModule && typeof schedModule.add === 'function') {
        return await schedModule.add(config);
      }

      // Fallback: append to schedules.json
      const schedDir = path.join(__dirname, 'scheduler');
      if (!fs.existsSync(schedDir)) fs.mkdirSync(schedDir, { recursive: true });
      const schedPath = path.join(schedDir, 'schedules.json');
      let schedules = [];
      try {
        if (fs.existsSync(schedPath)) {
          schedules = JSON.parse(fs.readFileSync(schedPath, 'utf-8'));
        }
      } catch (_) {}

      const entry = {
        id: `sched_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
        ...config,
        enabled: true,
        createdAt: Date.now(),
      };
      schedules.push(entry);
      fs.writeFileSync(schedPath, JSON.stringify(schedules, null, 2));
      return { ok: true, id: entry.id };
    } catch (err) {
      return { ok: false, error: err.message };
    }
  });

  ipcMain.handle('scheduler:remove', async (_event, id) => {
    try {
      const schedModule = tryRequire(path.join(__dirname, 'scheduler', 'index.js'));
      if (schedModule && typeof schedModule.remove === 'function') {
        return await schedModule.remove(id);
      }

      const schedPath = path.join(__dirname, 'scheduler', 'schedules.json');
      if (!fs.existsSync(schedPath)) return { ok: false, error: 'No schedules found' };
      let schedules = JSON.parse(fs.readFileSync(schedPath, 'utf-8'));
      const before = schedules.length;
      schedules = schedules.filter(s => s.id !== id);
      if (schedules.length === before) return { ok: false, error: `Schedule "${id}" not found` };
      fs.writeFileSync(schedPath, JSON.stringify(schedules, null, 2));
      return { ok: true };
    } catch (err) {
      return { ok: false, error: err.message };
    }
  });

  ipcMain.handle('scheduler:toggle', async (_event, id) => {
    try {
      const schedModule = tryRequire(path.join(__dirname, 'scheduler', 'index.js'));
      if (schedModule && typeof schedModule.toggle === 'function') {
        return await schedModule.toggle(id);
      }

      const schedPath = path.join(__dirname, 'scheduler', 'schedules.json');
      if (!fs.existsSync(schedPath)) return { ok: false, error: 'No schedules found' };
      const schedules = JSON.parse(fs.readFileSync(schedPath, 'utf-8'));
      const entry = schedules.find(s => s.id === id);
      if (!entry) return { ok: false, error: `Schedule "${id}" not found` };
      entry.enabled = !entry.enabled;
      fs.writeFileSync(schedPath, JSON.stringify(schedules, null, 2));
      return { ok: true, enabled: entry.enabled };
    } catch (err) {
      return { ok: false, error: err.message };
    }
  });

  // ════════════════════════════════════════════════════════════════════
  // SYSTEM
  // ════════════════════════════════════════════════════════════════════

  ipcMain.handle('system:doctor', async () => {
    const checks = {};

    // Check Ollama
    try {
      const http = require('http');
      checks.ollama = await new Promise((resolve) => {
        const req = http.request('http://localhost:11434/api/tags', { timeout: 3000 }, (res) => {
          let data = '';
          res.on('data', (d) => { data += d; });
          res.on('end', () => {
            try {
              const parsed = JSON.parse(data);
              resolve({
                status: 'ok',
                models: (parsed.models || []).map(m => m.name),
              });
            } catch (_) {
              resolve({ status: 'ok', models: [] });
            }
          });
        });
        req.on('error', () => resolve({ status: 'error', error: 'Ollama not reachable' }));
        req.on('timeout', () => { req.destroy(); resolve({ status: 'error', error: 'Timeout' }); });
        req.end();
      });
    } catch (_) {
      checks.ollama = { status: 'error', error: 'Check failed' };
    }

    // Check disk space
    try {
      const { execSync } = require('child_process');
      const df = execSync('df -h . 2>/dev/null || echo "unknown"', { encoding: 'utf-8' }).trim();
      const lines = df.split('\n');
      if (lines.length >= 2) {
        const parts = lines[1].split(/\s+/);
        checks.disk = {
          status: 'ok',
          filesystem: parts[0],
          size: parts[1],
          used: parts[2],
          available: parts[3],
          usePercent: parts[4],
        };
      } else {
        checks.disk = { status: 'ok', info: 'Could not parse disk info' };
      }
    } catch (_) {
      checks.disk = { status: 'error', error: 'Could not check disk' };
    }

    // Check agents directory
    const agentsDir = path.join(__dirname, 'agents');
    checks.agents = { status: fs.existsSync(agentsDir) ? 'ok' : 'missing', path: agentsDir };

    // Check skills directory
    const skillsDir = path.join(__dirname, 'skills');
    checks.skills = { status: fs.existsSync(skillsDir) ? 'ok' : 'missing', path: skillsDir };

    // Check memory directory
    const memoryDir = path.join(__dirname, 'memory');
    checks.memory = { status: fs.existsSync(memoryDir) ? 'ok' : 'missing', path: memoryDir };

    // Platform info
    checks.platform = {
      status: 'ok',
      os: process.platform,
      arch: process.arch,
      nodeVersion: process.version,
      electronVersion: process.versions.electron,
    };

    return checks;
  });

  ipcMain.handle('system:pick-folder', async () => {
    const win = typeof getMainWindow === 'function' ? getMainWindow() : null;
    const result = await dialog.showOpenDialog(win || {}, {
      properties: ['openDirectory'],
      title: 'Select a directory to index',
    });
    if (result.canceled || !result.filePaths.length) return null;
    return result.filePaths[0];
  });

  ipcMain.handle('system:pick-file', async () => {
    const win = typeof getMainWindow === 'function' ? getMainWindow() : null;
    const result = await dialog.showOpenDialog(win || {}, {
      properties: ['openFile'],
      title: 'Select a file to index',
      filters: [
        { name: 'Text Files', extensions: ['txt', 'md', 'json', 'csv', 'log', 'js', 'ts', 'py', 'html', 'css'] },
        { name: 'All Files', extensions: ['*'] },
      ],
    });
    if (result.canceled || !result.filePaths.length) return null;
    return result.filePaths[0];
  });

  // ── Load persisted agent configs on startup ─────────────────────
  try {
    const { app } = require('electron');
    const cfgPath = path.join(app.getPath('userData'), 'agent-configs.json');
    if (fs.existsSync(cfgPath)) {
      const stored = JSON.parse(fs.readFileSync(cfgPath, 'utf-8'));
      for (const [k, v] of Object.entries(stored)) {
        agentConfigs.set(k, v);
      }
    }
  } catch (_) {}
}

// ── Fallback agent run (uses Ollama directly) ───────────────────────
async function fallbackAgentRun(agentId, input) {
  const http = require('http');
  const cfg = agentConfigs ? agentConfigs.get(agentId) : {};
  const model = cfg?.model || 'llama3.2:3b';
  const systemPrompt = cfg?.systemPrompt || `You are the ${agentId} agent in Nova AI. Be helpful and concise.`;

  const messages = [
    { role: 'system', content: systemPrompt },
    { role: 'user', content: input },
  ];

  const body = JSON.stringify({ model, messages, stream: false });

  return new Promise((resolve) => {
    const req = http.request({
      hostname: '127.0.0.1',
      port: 11434,
      path: '/api/chat',
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
      timeout: 120000,
    }, (res) => {
      let data = '';
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => {
        try {
          const parsed = JSON.parse(data);
          resolve({
            output: parsed.message?.content || 'No response',
            steps: [],
            metadata: { agent: agentId, model, fallback: true },
          });
        } catch (_) {
          resolve({ output: 'Failed to parse Ollama response', error: 'parse_error', steps: [], metadata: { error: true } });
        }
      });
    });
    req.on('error', (err) => {
      resolve({ output: `Ollama error: ${err.message}`, error: err.message, steps: [], metadata: { error: true } });
    });
    req.on('timeout', () => {
      req.destroy();
      resolve({ output: 'Ollama request timed out', error: 'timeout', steps: [], metadata: { error: true } });
    });
    req.write(body);
    req.end();
  });
}

// ── Utility ─────────────────────────────────────────────────────────
function formatBytes(bytes) {
  if (bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return (bytes / Math.pow(1024, i)).toFixed(i > 0 ? 1 : 0) + ' ' + units[i];
}

module.exports = { registerDesktopIPC };
