#!/usr/bin/env node
'use strict';

/**
 * Nova CLI — Command-line interface for Nova AI
 * 
 * Usage: node nova-cli.js <command> [args...]
 * 
 * Pure Node.js, zero external dependencies.
 */

const readline = require('readline');
const http = require('http');
const https = require('https');
const fs = require('fs');
const path = require('path');
const os = require('os');

// ── Configuration ───────────────────────────────────────────────────

const OLLAMA_URL = process.env.NOVA_OLLAMA_URL || 'http://localhost:11434';
const OLLAMA_MODEL = process.env.NOVA_MODEL || 'llama3.2:3b';
const VERSION = '2.0.0';
const NOVA_DIR = path.join(os.homedir(), '.nova');

// ── ANSI Colors ─────────────────────────────────────────────────────

const c = {
  reset: '\x1b[0m',
  bold: '\x1b[1m',
  dim: '\x1b[2m',
  italic: '\x1b[3m',
  underline: '\x1b[4m',

  red: '\x1b[31m',
  green: '\x1b[32m',
  yellow: '\x1b[33m',
  blue: '\x1b[34m',
  magenta: '\x1b[35m',
  cyan: '\x1b[36m',
  white: '\x1b[37m',
  gray: '\x1b[90m',

  bgRed: '\x1b[41m',
  bgGreen: '\x1b[42m',
  bgBlue: '\x1b[44m',
};

function green(s)   { return `${c.green}${s}${c.reset}`; }
function blue(s)    { return `${c.blue}${s}${c.reset}`; }
function red(s)     { return `${c.red}${s}${c.reset}`; }
function yellow(s)  { return `${c.yellow}${s}${c.reset}`; }
function cyan(s)    { return `${c.cyan}${s}${c.reset}`; }
function gray(s)    { return `${c.gray}${s}${c.reset}`; }
function bold(s)    { return `${c.bold}${s}${c.reset}`; }
function dim(s)     { return `${c.dim}${s}${c.reset}`; }

// ── Spinner ─────────────────────────────────────────────────────────

const SPINNER_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];
let _spinnerTimer = null;
let _spinnerFrame = 0;

function startSpinner(msg = 'Thinking') {
  if (_spinnerTimer) return;
  _spinnerFrame = 0;
  _spinnerTimer = setInterval(() => {
    const frame = SPINNER_FRAMES[_spinnerFrame % SPINNER_FRAMES.length];
    process.stdout.write(`\r${c.cyan}${frame}${c.reset} ${dim(msg)}   `);
    _spinnerFrame++;
  }, 80);
}

function stopSpinner() {
  if (_spinnerTimer) {
    clearInterval(_spinnerTimer);
    _spinnerTimer = null;
    process.stdout.write('\r\x1b[K'); // Clear line
  }
}

// ── Ollama Client ───────────────────────────────────────────────────

/**
 * Check if Ollama is running
 * @returns {Promise<boolean>}
 */
function checkOllama() {
  return new Promise((resolve) => {
    const parsed = new URL(OLLAMA_URL);
    const transport = parsed.protocol === 'https:' ? https : http;

    const req = transport.get(`${OLLAMA_URL}/api/tags`, { timeout: 3000 }, (res) => {
      let data = '';
      res.on('data', chunk => { data += chunk; });
      res.on('end', () => resolve(res.statusCode === 200));
    });
    req.on('error', () => resolve(false));
    req.on('timeout', () => { req.destroy(); resolve(false); });
  });
}

/**
 * Get available models from Ollama
 * @returns {Promise<string[]>}
 */
function listModels() {
  return new Promise((resolve, reject) => {
    const parsed = new URL(OLLAMA_URL);
    const transport = parsed.protocol === 'https:' ? https : http;

    const req = transport.get(`${OLLAMA_URL}/api/tags`, { timeout: 5000 }, (res) => {
      let data = '';
      res.on('data', chunk => { data += chunk; });
      res.on('end', () => {
        try {
          const json = JSON.parse(data);
          resolve((json.models || []).map(m => m.name));
        } catch {
          resolve([]);
        }
      });
    });
    req.on('error', () => resolve([]));
    req.on('timeout', () => { req.destroy(); resolve([]); });
  });
}

/**
 * Stream a chat completion from Ollama
 * @param {object[]} messages - Chat messages
 * @param {object} opts - { model?, system?, onToken? }
 * @returns {Promise<string>} Full response text
 */
function ollamaChat(messages, opts = {}) {
  const model = opts.model || OLLAMA_MODEL;
  const system = opts.system || 'You are Nova, a helpful AI assistant. Be concise and direct.';

  return new Promise((resolve, reject) => {
    const parsed = new URL(OLLAMA_URL);
    const transport = parsed.protocol === 'https:' ? https : http;

    const body = JSON.stringify({
      model,
      messages: [
        { role: 'system', content: system },
        ...messages,
      ],
      stream: true,
    });

    const reqOpts = {
      hostname: parsed.hostname,
      port: parsed.port,
      path: '/api/chat',
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(body),
      },
      timeout: 120000,
    };

    const req = transport.request(reqOpts, (res) => {
      let fullResponse = '';
      let buffer = '';

      res.on('data', (chunk) => {
        buffer += chunk.toString();
        const lines = buffer.split('\n');
        buffer = lines.pop(); // Keep incomplete line in buffer

        for (const line of lines) {
          if (!line.trim()) continue;
          try {
            const json = JSON.parse(line);
            if (json.message && json.message.content) {
              const token = json.message.content;
              fullResponse += token;
              if (opts.onToken) opts.onToken(token);
            }
            if (json.done) {
              // Process any remaining buffer
              if (buffer.trim()) {
                try {
                  const lastJson = JSON.parse(buffer);
                  if (lastJson.message && lastJson.message.content) {
                    fullResponse += lastJson.message.content;
                    if (opts.onToken) opts.onToken(lastJson.message.content);
                  }
                } catch (_) {}
              }
            }
          } catch (_) {
            // Skip malformed lines
          }
        }
      });

      res.on('end', () => {
        // Process final buffer
        if (buffer.trim()) {
          try {
            const json = JSON.parse(buffer);
            if (json.message && json.message.content) {
              fullResponse += json.message.content;
              if (opts.onToken) opts.onToken(json.message.content);
            }
          } catch (_) {}
        }
        resolve(fullResponse);
      });

      res.on('error', reject);
    });

    req.on('error', reject);
    req.on('timeout', () => {
      req.destroy();
      reject(new Error('Request timed out'));
    });

    req.write(body);
    req.end();
  });
}

/**
 * One-shot generate (non-chat, for quick tasks)
 */
function ollamaGenerate(prompt, opts = {}) {
  const model = opts.model || OLLAMA_MODEL;

  return new Promise((resolve, reject) => {
    const parsed = new URL(OLLAMA_URL);
    const transport = parsed.protocol === 'https:' ? https : http;

    const body = JSON.stringify({
      model,
      prompt,
      stream: false,
    });

    const reqOpts = {
      hostname: parsed.hostname,
      port: parsed.port,
      path: '/api/generate',
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(body),
      },
      timeout: 120000,
    };

    const req = transport.request(reqOpts, (res) => {
      let data = '';
      res.on('data', chunk => { data += chunk; });
      res.on('end', () => {
        try {
          const json = JSON.parse(data);
          resolve(json.response || '');
        } catch {
          reject(new Error('Invalid response from Ollama'));
        }
      });
    });

    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('Request timed out')); });
    req.write(body);
    req.end();
  });
}

// ── Agent System Prompts ────────────────────────────────────────────

const AGENT_PROMPTS = {
  default: 'You are Nova, a helpful AI assistant. Be concise, direct, and useful.',

  research: `You are Nova in research mode. Your job is to provide comprehensive, well-structured research on any topic.
Structure your response with:
- Executive summary (2-3 sentences)
- Key findings (bullet points)
- Detailed analysis
- Sources/references if applicable
Be thorough but organized.`,

  code: `You are Nova in coding mode. You are an expert programmer.
- Write clean, well-commented code
- Explain your approach briefly
- Handle edge cases
- Use best practices for the language
- If the task is ambiguous, state your assumptions`,

  digest: `You are Nova preparing a morning digest. Summarize the key things the user should know about today:
- Any pending tasks or reminders
- Weather outlook (if known)
- Calendar events (if known)
- News highlights (if known)
Keep it brief and actionable.`,
};

// ── Memory Module (lazy load) ───────────────────────────────────────

let _memory = null;

function getMemory() {
  if (_memory) return _memory;
  try {
    _memory = require('../memory');
    return _memory;
  } catch (err) {
    return null;
  }
}

// ── Scheduler Module (lazy load) ────────────────────────────────────

let _scheduler = null;

function getScheduler() {
  if (_scheduler) return _scheduler;
  try {
    _scheduler = require('../scheduler');
    return _scheduler;
  } catch (err) {
    return null;
  }
}

// ── Commands ────────────────────────────────────────────────────────

async function cmdAsk(question, agentType = 'default') {
  const available = await checkOllama();
  if (!available) {
    console.error(red('\n  ✗ Ollama is not running!'));
    console.error(gray('    Start it with: ollama serve'));
    console.error(gray(`    Expected at: ${OLLAMA_URL}\n`));
    process.exit(1);
  }

  const system = AGENT_PROMPTS[agentType] || AGENT_PROMPTS.default;

  startSpinner('Thinking');
  let firstToken = true;

  try {
    const response = await ollamaChat(
      [{ role: 'user', content: question }],
      {
        system,
        onToken: (token) => {
          if (firstToken) {
            stopSpinner();
            process.stdout.write(`\n${c.green}Nova${c.reset} ${c.dim}›${c.reset} `);
            firstToken = false;
          }
          process.stdout.write(token);
        },
      }
    );

    if (firstToken) {
      stopSpinner();
      console.log(`\n${green('Nova')} ${dim('›')} ${response}`);
    } else {
      console.log('\n');
    }
  } catch (err) {
    stopSpinner();
    console.error(red(`\n  ✗ Error: ${err.message}`));
    process.exit(1);
  }
}

async function cmdChat() {
  const available = await checkOllama();
  if (!available) {
    console.error(red('\n  ✗ Ollama is not running!'));
    console.error(gray('    Start it with: ollama serve'));
    console.error(gray(`    Expected at: ${OLLAMA_URL}\n`));
    process.exit(1);
  }

  console.log(`\n${bold(green('Nova AI'))} ${dim(`v${VERSION}`)}`);
  console.log(dim(`Model: ${OLLAMA_MODEL} • Type /help for commands\n`));

  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
    prompt: `${c.blue}you${c.reset} ${c.dim}›${c.reset} `,
  });

  let messages = [];
  let currentAgent = 'default';

  rl.prompt();

  rl.on('line', async (line) => {
    const input = line.trim();
    if (!input) { rl.prompt(); return; }

    // Slash commands
    if (input.startsWith('/')) {
      const parts = input.slice(1).split(/\s+/);
      const cmd = parts[0].toLowerCase();

      switch (cmd) {
        case 'exit':
        case 'quit':
        case 'q':
          console.log(dim('\nGoodbye! 👋\n'));
          process.exit(0);
          break;

        case 'clear':
          messages = [];
          console.log(blue('  ↻ Conversation cleared'));
          break;

        case 'agent':
          const agentName = parts[1];
          if (!agentName) {
            console.log(blue(`  Current agent: ${currentAgent}`));
            console.log(dim(`  Available: ${Object.keys(AGENT_PROMPTS).join(', ')}`));
          } else if (AGENT_PROMPTS[agentName]) {
            currentAgent = agentName;
            messages = []; // Reset conversation on agent switch
            console.log(blue(`  ✓ Switched to ${agentName} agent`));
          } else {
            console.log(red(`  ✗ Unknown agent: ${agentName}`));
            console.log(dim(`  Available: ${Object.keys(AGENT_PROMPTS).join(', ')}`));
          }
          break;

        case 'model':
          const models = await listModels();
          if (models.length === 0) {
            console.log(yellow('  No models found (is Ollama running?)'));
          } else {
            console.log(blue('  Available models:'));
            for (const m of models) {
              const marker = m === OLLAMA_MODEL ? green(' ← current') : '';
              console.log(`    ${m}${marker}`);
            }
          }
          break;

        case 'history':
          if (messages.length === 0) {
            console.log(dim('  No messages yet'));
          } else {
            console.log(blue(`  ${messages.length} messages in conversation`));
          }
          break;

        case 'help':
          console.log(`
  ${bold('Commands:')}
    ${cyan('/exit')}          Exit Nova
    ${cyan('/clear')}         Clear conversation
    ${cyan('/agent <name>')}  Switch agent (${Object.keys(AGENT_PROMPTS).join(', ')})
    ${cyan('/model')}         List available models
    ${cyan('/history')}       Show conversation length
    ${cyan('/help')}          Show this help
`);
          break;

        default:
          console.log(red(`  ✗ Unknown command: /${cmd}`));
          console.log(dim('  Type /help for available commands'));
      }

      rl.prompt();
      return;
    }

    // Regular message — send to Ollama
    messages.push({ role: 'user', content: input });

    rl.pause();
    startSpinner('Thinking');
    let firstToken = true;

    try {
      const response = await ollamaChat(messages, {
        system: AGENT_PROMPTS[currentAgent],
        onToken: (token) => {
          if (firstToken) {
            stopSpinner();
            process.stdout.write(`${c.green}Nova${c.reset} ${c.dim}›${c.reset} `);
            firstToken = false;
          }
          process.stdout.write(token);
        },
      });

      if (firstToken) {
        stopSpinner();
        console.log(`${green('Nova')} ${dim('›')} ${response}`);
      } else {
        console.log('');
      }

      messages.push({ role: 'assistant', content: response });
    } catch (err) {
      stopSpinner();
      console.error(red(`  ✗ Error: ${err.message}`));
    }

    rl.resume();
    rl.prompt();
  });

  rl.on('close', () => {
    console.log(dim('\nGoodbye! 👋\n'));
    process.exit(0);
  });
}

async function cmdResearch(topic) {
  console.log(blue(`\n  🔍 Researching: ${topic}\n`));
  await cmdAsk(topic, 'research');
}

async function cmdCode(task) {
  console.log(blue(`\n  💻 Coding: ${task}\n`));
  await cmdAsk(task, 'code');
}

async function cmdDigest() {
  console.log(blue('\n  📋 Generating morning digest...\n'));
  const prompt = `Generate my morning digest for ${new Date().toLocaleDateString('en-US', {
    weekday: 'long',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  })}. Include a motivational note.`;
  await cmdAsk(prompt, 'digest');
}

async function cmdMemory(subcmd, args) {
  const memory = getMemory();
  if (!memory) {
    console.error(red('\n  ✗ Memory module not available'));
    console.error(gray('    Check that memory/ directory exists with required files\n'));
    return;
  }

  switch (subcmd) {
    case 'index': {
      const target = args[0];
      if (!target) {
        console.error(red('  ✗ Usage: nova memory index <path>'));
        return;
      }
      const absPath = path.resolve(target);
      const stat = fs.existsSync(absPath) ? fs.statSync(absPath) : null;

      if (!stat) {
        console.error(red(`  ✗ Path not found: ${absPath}`));
        return;
      }

      startSpinner('Indexing');
      try {
        if (stat.isDirectory()) {
          const result = await memory.indexDir(absPath);
          stopSpinner();
          console.log(green(`\n  ✓ Indexed ${result.files} files (${result.chunks} chunks)`));
          if (result.skipped > 0) {
            console.log(dim(`    Skipped: ${result.skipped} unsupported files`));
          }
          if (result.errors.length > 0) {
            console.log(yellow(`    Errors: ${result.errors.length}`));
            for (const err of result.errors.slice(0, 5)) {
              console.log(red(`      ${err}`));
            }
          }
        } else {
          const result = await memory.index(absPath);
          stopSpinner();
          console.log(green(`\n  ✓ Indexed ${result.indexed} chunks from ${path.basename(absPath)}`));
          console.log(dim(`    Embedding source: ${result.embedSource}`));
        }
      } catch (err) {
        stopSpinner();
        console.error(red(`\n  ✗ Indexing failed: ${err.message}`));
      }
      console.log('');
      break;
    }

    case 'search': {
      const query = args.join(' ');
      if (!query) {
        console.error(red('  ✗ Usage: nova memory search "query"'));
        return;
      }

      startSpinner('Searching');
      try {
        const results = await memory.recall(query, 5);
        stopSpinner();

        if (results.length === 0) {
          console.log(yellow('\n  No results found.\n'));
        } else {
          console.log(bold(`\n  Found ${results.length} results:\n`));
          for (let i = 0; i < results.length; i++) {
            const { doc, score } = results[i];
            const scoreStr = (score * 100).toFixed(1);
            const preview = doc.content.slice(0, 120).replace(/\n/g, ' ');
            const source = doc.metadata.source
              ? dim(` (${path.basename(doc.metadata.source)})`)
              : '';

            console.log(`  ${cyan(`${i + 1}.`)} ${bold(`[${scoreStr}%]`)} ${preview}...${source}`);
            console.log(gray(`     ID: ${doc.id}`));
          }
          console.log('');
        }
      } catch (err) {
        stopSpinner();
        console.error(red(`\n  ✗ Search failed: ${err.message}\n`));
      }
      break;
    }

    case 'stats': {
      try {
        const s = memory.stats();
        console.log(bold('\n  Memory Statistics:'));
        console.log(`    Documents:    ${green(String(s.totalDocuments))}`);
        console.log(`    Embedded:     ${green(String(s.withEmbeddings))}`);
        console.log(`    Disk usage:   ${s.diskSize}`);
        console.log(`    Last indexed: ${s.lastIndexed || dim('never')}`);
        console.log(`    Store path:   ${dim(s.storePath)}`);
        console.log('');
      } catch (err) {
        console.error(red(`\n  ✗ Stats failed: ${err.message}\n`));
      }
      break;
    }

    case 'forget': {
      const id = args[0];
      if (!id) {
        console.error(red('  ✗ Usage: nova memory forget <id>'));
        return;
      }
      const removed = memory.forget(id);
      if (removed) {
        console.log(green(`\n  ✓ Forgot memory: ${id}\n`));
      } else {
        console.log(yellow(`\n  Memory not found: ${id}\n`));
      }
      break;
    }

    default:
      console.log(`
  ${bold('Memory Commands:')}
    ${cyan('nova memory index <path>')}    Index a file or directory
    ${cyan('nova memory search "query"')}  Semantic search
    ${cyan('nova memory stats')}           Show statistics
    ${cyan('nova memory forget <id>')}     Delete a memory
`);
  }
}

async function cmdSchedule(subcmd, args) {
  const scheduler = getScheduler();
  if (!scheduler) {
    console.error(red('\n  ✗ Scheduler module not available'));
    return;
  }

  switch (subcmd) {
    case 'list': {
      const jobList = scheduler.listJobs();
      if (jobList.length === 0) {
        console.log(dim('\n  No scheduled jobs.\n'));
      } else {
        console.log(bold(`\n  Scheduled Jobs (${jobList.length}):\n`));
        for (const job of jobList) {
          const status = job.enabled ? green('●') : red('○');
          const schedule = job.schedule || `every ${humanDuration(job.intervalMs)}`;
          const nextRun = job.nextRun ? dim(` → next: ${new Date(job.nextRun).toLocaleString()}`) : '';
          console.log(`  ${status} ${bold(job.name)} ${gray(`[${job.id}]`)}`);
          console.log(`    Agent: ${job.agentName} • Schedule: ${schedule}${nextRun}`);
          if (job.stats && job.stats.totalRuns > 0) {
            console.log(dim(`    Runs: ${job.stats.totalRuns} (${job.stats.successRate} success)`));
          }
        }
        console.log('');
      }
      break;
    }

    case 'add': {
      // Interactive mode for adding jobs
      const rl = readline.createInterface({
        input: process.stdin,
        output: process.stdout,
      });

      const ask = (q) => new Promise(resolve => rl.question(q, resolve));

      console.log(bold('\n  Add Scheduled Job\n'));

      try {
        const name = await ask(blue('  Name: '));
        const agentName = (await ask(blue('  Agent (default): '))) || 'default';
        const input = await ask(blue('  Input/prompt: '));
        const schedule = await ask(blue('  Cron schedule (e.g. "0 8 * * *" for 8 AM daily): '));

        const job = scheduler.addJob({
          name: name.trim(),
          agentName: agentName.trim(),
          input: input.trim(),
          schedule: schedule.trim(),
        });

        console.log(green(`\n  ✓ Job created: ${job.name} [${job.id}]`));
        if (job.nextRun) {
          console.log(dim(`    Next run: ${new Date(job.nextRun).toLocaleString()}`));
        }
      } catch (err) {
        console.error(red(`\n  ✗ Failed: ${err.message}`));
      }

      rl.close();
      console.log('');
      break;
    }

    case 'remove': {
      const id = args[0];
      if (!id) {
        console.error(red('  ✗ Usage: nova schedule remove <id>'));
        return;
      }
      const removed = scheduler.removeJob(id);
      if (removed) {
        console.log(green(`\n  ✓ Removed job: ${id}\n`));
      } else {
        console.log(yellow(`\n  Job not found: ${id}\n`));
      }
      break;
    }

    case 'enable': {
      const id = args[0];
      if (!id) { console.error(red('  ✗ Usage: nova schedule enable <id>')); return; }
      if (scheduler.enableJob(id)) {
        console.log(green(`\n  ✓ Enabled job: ${id}\n`));
      } else {
        console.log(yellow(`\n  Job not found: ${id}\n`));
      }
      break;
    }

    case 'disable': {
      const id = args[0];
      if (!id) { console.error(red('  ✗ Usage: nova schedule disable <id>')); return; }
      if (scheduler.disableJob(id)) {
        console.log(green(`\n  ✓ Disabled job: ${id}\n`));
      } else {
        console.log(yellow(`\n  Job not found: ${id}\n`));
      }
      break;
    }

    default:
      console.log(`
  ${bold('Schedule Commands:')}
    ${cyan('nova schedule list')}           List all jobs
    ${cyan('nova schedule add')}            Add a job (interactive)
    ${cyan('nova schedule remove <id>')}    Remove a job
    ${cyan('nova schedule enable <id>')}    Enable a job
    ${cyan('nova schedule disable <id>')}   Disable a job
`);
  }
}

async function cmdSkills(subcmd, args) {
  const skillsDir = path.join(__dirname, '..', 'skills', 'builtin');

  switch (subcmd) {
    case 'list': {
      console.log(bold('\n  Installed Skills:\n'));

      // Check builtin skills
      if (fs.existsSync(skillsDir)) {
        try {
          const entries = fs.readdirSync(skillsDir, { withFileTypes: true });
          const skills = entries.filter(e => e.isDirectory() || e.name.endsWith('.js'));
          if (skills.length === 0) {
            console.log(dim('    No builtin skills installed'));
          } else {
            for (const entry of skills) {
              const name = entry.name.replace('.js', '');
              console.log(`    ${green('●')} ${name} ${dim('(builtin)')}`);
            }
          }
        } catch (err) {
          console.log(dim('    Could not read skills directory'));
        }
      } else {
        console.log(dim('    No skills directory found'));
      }

      // Check user skills
      const userSkillsDir = path.join(NOVA_DIR, 'skills');
      if (fs.existsSync(userSkillsDir)) {
        try {
          const entries = fs.readdirSync(userSkillsDir, { withFileTypes: true });
          for (const entry of entries.filter(e => e.isDirectory())) {
            console.log(`    ${cyan('●')} ${entry.name} ${dim('(user)')}`);
          }
        } catch (_) {}
      }

      console.log('');
      break;
    }

    case 'install': {
      const url = args[0];
      if (!url) {
        console.error(red('  ✗ Usage: nova skills install <github-url>'));
        return;
      }
      console.log(yellow(`\n  ⚠ Skill installation from URL is not yet implemented.`));
      console.log(dim(`    URL: ${url}`));
      console.log(dim(`    Skills will be installed to: ${path.join(NOVA_DIR, 'skills')}\n`));
      break;
    }

    default:
      console.log(`
  ${bold('Skills Commands:')}
    ${cyan('nova skills list')}              List installed skills
    ${cyan('nova skills install <url>')}     Install from GitHub
`);
  }
}

async function cmdAgents(subcmd, args) {
  switch (subcmd) {
    case 'list': {
      console.log(bold('\n  Available Agents:\n'));
      for (const [name, desc] of Object.entries(AGENT_PROMPTS)) {
        const truncDesc = desc.split('\n')[0].slice(0, 80);
        console.log(`    ${green('●')} ${bold(name)}`);
        console.log(dim(`      ${truncDesc}`));
      }
      console.log('');
      break;
    }

    case 'run': {
      const agentName = args[0];
      if (!agentName) {
        console.error(red('  ✗ Usage: nova agents run <name>'));
        return;
      }
      if (!AGENT_PROMPTS[agentName]) {
        console.error(red(`  ✗ Unknown agent: ${agentName}`));
        console.error(dim(`  Available: ${Object.keys(AGENT_PROMPTS).join(', ')}`));
        return;
      }
      console.log(blue(`\n  Starting ${agentName} agent...\n`));
      // Set agent and fall into chat
      process.env._NOVA_AGENT = agentName;
      await cmdChat();
      break;
    }

    default:
      console.log(`
  ${bold('Agent Commands:')}
    ${cyan('nova agents list')}          List available agents
    ${cyan('nova agents run <name>')}    Run an agent interactively
`);
  }
}

async function cmdDoctor() {
  console.log(bold('\n  Nova Doctor — System Health Check\n'));

  // 1. Ollama
  process.stdout.write(`  Ollama (${OLLAMA_URL})... `);
  const ollamaUp = await checkOllama();
  if (ollamaUp) {
    console.log(green('✓ running'));
    const models = await listModels();
    console.log(dim(`    Models: ${models.length > 0 ? models.join(', ') : 'none'}`));
    const hasDefault = models.some(m => m.startsWith(OLLAMA_MODEL.split(':')[0]));
    if (!hasDefault) {
      console.log(yellow(`    ⚠ Default model "${OLLAMA_MODEL}" not found`));
      console.log(dim(`      Run: ollama pull ${OLLAMA_MODEL}`));
    }
  } else {
    console.log(red('✗ not running'));
    console.log(dim('    Start with: ollama serve'));
  }

  // 2. Nova directory
  process.stdout.write(`  Nova config dir... `);
  if (fs.existsSync(NOVA_DIR)) {
    console.log(green(`✓ ${NOVA_DIR}`));
  } else {
    console.log(yellow(`○ not created yet (${NOVA_DIR})`));
    console.log(dim('    Will be created on first use'));
  }

  // 3. Memory store
  process.stdout.write(`  Memory store... `);
  const memoryPath = path.join(NOVA_DIR, 'memory.json');
  if (fs.existsSync(memoryPath)) {
    try {
      const stat = fs.statSync(memoryPath);
      const data = JSON.parse(fs.readFileSync(memoryPath, 'utf-8'));
      const docCount = Object.keys(data.documents || {}).length;
      console.log(green(`✓ ${docCount} documents (${formatBytes(stat.size)})`));
    } catch {
      console.log(yellow('⚠ exists but corrupt'));
    }
  } else {
    console.log(dim('○ empty (no memories yet)'));
  }

  // 4. Scheduler
  process.stdout.write(`  Scheduler... `);
  const schedulerPath = path.join(NOVA_DIR, 'scheduler.json');
  if (fs.existsSync(schedulerPath)) {
    try {
      const data = JSON.parse(fs.readFileSync(schedulerPath, 'utf-8'));
      const jobCount = Object.keys(data.jobs || {}).length;
      console.log(green(`✓ ${jobCount} jobs configured`));
    } catch {
      console.log(yellow('⚠ exists but corrupt'));
    }
  } else {
    console.log(dim('○ no jobs'));
  }

  // 5. Disk space
  process.stdout.write(`  Disk space... `);
  try {
    const homeStat = fs.statfsSync ? fs.statfsSync(os.homedir()) : null;
    if (homeStat) {
      const freeGB = (homeStat.bfree * homeStat.bsize / (1024 ** 3)).toFixed(1);
      const totalGB = (homeStat.blocks * homeStat.bsize / (1024 ** 3)).toFixed(1);
      const usedPct = ((1 - homeStat.bfree / homeStat.blocks) * 100).toFixed(1);
      console.log(usedPct > 90 ? yellow(`⚠ ${freeGB}GB free / ${totalGB}GB (${usedPct}% used)`) : green(`✓ ${freeGB}GB free / ${totalGB}GB`));
    } else {
      console.log(dim('○ statfs not available'));
    }
  } catch {
    console.log(dim('○ could not check'));
  }

  // 6. Node.js version
  console.log(`  Node.js: ${green(process.version)}`);

  console.log('');
}

function cmdVersion() {
  console.log(`\n  ${bold(green('Nova AI'))} v${VERSION}`);
  console.log(dim(`  Model: ${OLLAMA_MODEL}`));
  console.log(dim(`  Ollama: ${OLLAMA_URL}`));
  console.log(dim(`  Config: ${NOVA_DIR}`));
  console.log(dim(`  Node: ${process.version}\n`));
}

function cmdHelp() {
  console.log(`
  ${bold(green('Nova AI'))} ${dim(`v${VERSION}`)} — Your Personal AI

  ${bold('Usage:')} nova <command> [args...]

  ${bold('Chat:')}
    ${cyan('nova')}                        Start interactive chat
    ${cyan('nova chat')}                   Same as above
    ${cyan('nova ask "question"')}         One-shot question
    ${cyan('nova research "topic"')}       Deep research
    ${cyan('nova code "task"')}            Code generation
    ${cyan('nova digest')}                 Morning digest

  ${bold('Memory:')}
    ${cyan('nova memory index <path>')}    Index a file or directory
    ${cyan('nova memory search "query"')}  Semantic search
    ${cyan('nova memory stats')}           Statistics
    ${cyan('nova memory forget <id>')}     Delete a memory

  ${bold('Skills:')}
    ${cyan('nova skills list')}            List installed skills
    ${cyan('nova skills install <url>')}   Install from GitHub

  ${bold('Agents:')}
    ${cyan('nova agents list')}            List available agents
    ${cyan('nova agents run <name>')}      Run an agent interactively

  ${bold('Schedule:')}
    ${cyan('nova schedule list')}          List scheduled jobs
    ${cyan('nova schedule add')}           Add a new job (interactive)
    ${cyan('nova schedule remove <id>')}   Remove a job

  ${bold('System:')}
    ${cyan('nova doctor')}                 Check system health
    ${cyan('nova version')}                Show version
    ${cyan('nova help')}                   This help message

  ${bold('Environment:')}
    ${dim('NOVA_OLLAMA_URL')}   Ollama URL (default: http://localhost:11434)
    ${dim('NOVA_MODEL')}        Model name (default: llama3.2:3b)
`);
}

// ── Utility ─────────────────────────────────────────────────────────

function humanDuration(ms) {
  if (!ms || ms < 0) return 'now';
  const seconds = Math.floor(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ${minutes % 60}m`;
  const days = Math.floor(hours / 24);
  return `${days}d ${hours % 24}h`;
}

function formatBytes(bytes) {
  if (bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return (bytes / Math.pow(1024, i)).toFixed(1) + ' ' + units[i];
}

// ── Main Entry Point ────────────────────────────────────────────────

async function main() {
  const args = process.argv.slice(2);
  const command = (args[0] || '').toLowerCase();
  const subArgs = args.slice(1);

  try {
    switch (command) {
      case '':
      case 'chat':
        await cmdChat();
        break;

      case 'ask':
        if (subArgs.length === 0) {
          console.error(red('\n  ✗ Usage: nova ask "your question"\n'));
          process.exit(1);
        }
        await cmdAsk(subArgs.join(' '));
        break;

      case 'research':
        if (subArgs.length === 0) {
          console.error(red('\n  ✗ Usage: nova research "topic"\n'));
          process.exit(1);
        }
        await cmdResearch(subArgs.join(' '));
        break;

      case 'code':
        if (subArgs.length === 0) {
          console.error(red('\n  ✗ Usage: nova code "task"\n'));
          process.exit(1);
        }
        await cmdCode(subArgs.join(' '));
        break;

      case 'digest':
        await cmdDigest();
        break;

      case 'memory':
        await cmdMemory(subArgs[0], subArgs.slice(1));
        break;

      case 'skills':
        await cmdSkills(subArgs[0], subArgs.slice(1));
        break;

      case 'agents':
        await cmdAgents(subArgs[0], subArgs.slice(1));
        break;

      case 'schedule':
        await cmdSchedule(subArgs[0], subArgs.slice(1));
        break;

      case 'doctor':
        await cmdDoctor();
        break;

      case 'version':
      case '--version':
      case '-v':
        cmdVersion();
        break;

      case 'help':
      case '--help':
      case '-h':
        cmdHelp();
        break;

      default:
        console.error(red(`\n  ✗ Unknown command: ${command}`));
        console.error(dim('  Run "nova help" for available commands\n'));
        process.exit(1);
    }
  } catch (err) {
    stopSpinner();
    console.error(red(`\n  ✗ Fatal error: ${err.message}`));
    if (process.env.DEBUG) console.error(err.stack);
    process.exit(1);
  }
}

main();
