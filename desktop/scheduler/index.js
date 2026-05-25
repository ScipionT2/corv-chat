'use strict';

/**
 * Nova Scheduler — Cron-like job scheduler
 * 
 * Supports cron expressions (min hour dom month dow) and interval-based scheduling.
 * Jobs persist to ~/.nova/scheduler.json
 */

const fs = require('fs');
const path = require('path');
const os = require('os');
const crypto = require('crypto');
const jobs = require('./jobs');

const NOVA_DIR = path.join(os.homedir(), '.nova');
const SCHEDULER_PATH = path.join(NOVA_DIR, 'scheduler.json');

let _config = null;
let _timers = new Map();  // jobId → timer handle
let _running = false;

// ── Helpers ─────────────────────────────────────────────────────────

function ensureDir() {
  if (!fs.existsSync(NOVA_DIR)) {
    fs.mkdirSync(NOVA_DIR, { recursive: true });
  }
}

function generateId() {
  return 'job_' + crypto.randomBytes(6).toString('hex');
}

// ── Cron Parser ─────────────────────────────────────────────────────

/**
 * Parse a single cron field
 * @param {string} field - e.g. '*', '5', '1-3', 'star/15', '1,3,5'
 * @param {number} min - Minimum value
 * @param {number} max - Maximum value
 * @returns {number[]} Array of matching values
 */
function parseCronField(field, min, max) {
  const values = new Set();

  const parts = field.split(',');
  for (const part of parts) {
    // */N — every N
    if (part.startsWith('*/')) {
      const step = parseInt(part.slice(2), 10);
      if (isNaN(step) || step <= 0) throw new Error(`Invalid cron step: ${part}`);
      for (let i = min; i <= max; i += step) values.add(i);
    }
    // N-M — range
    else if (part.includes('-')) {
      const [startStr, endStr] = part.split('-');
      const start = parseInt(startStr, 10);
      const end = parseInt(endStr, 10);
      if (isNaN(start) || isNaN(end)) throw new Error(`Invalid cron range: ${part}`);
      for (let i = start; i <= end; i++) values.add(i);
    }
    // * — all
    else if (part === '*') {
      for (let i = min; i <= max; i++) values.add(i);
    }
    // N — specific value
    else {
      const val = parseInt(part, 10);
      if (isNaN(val)) throw new Error(`Invalid cron value: ${part}`);
      values.add(val);
    }
  }

  return [...values].sort((a, b) => a - b);
}

/**
 * Parse a cron expression: "min hour dom month dow"
 * @param {string} expr
 * @returns {object} { minutes, hours, daysOfMonth, months, daysOfWeek }
 */
function parseCron(expr) {
  const parts = expr.trim().split(/\s+/);
  if (parts.length !== 5) {
    throw new Error(`Invalid cron expression (expected 5 fields): "${expr}"`);
  }

  return {
    minutes: parseCronField(parts[0], 0, 59),
    hours: parseCronField(parts[1], 0, 23),
    daysOfMonth: parseCronField(parts[2], 1, 31),
    months: parseCronField(parts[3], 1, 12),
    daysOfWeek: parseCronField(parts[4], 0, 6),  // 0 = Sunday
  };
}

/**
 * Get the next matching time for a cron expression
 * @param {string} cronExpr
 * @param {Date} from - Start time (default: now)
 * @returns {Date} Next run time
 */
function getNextCronTime(cronExpr, from = new Date()) {
  const cron = parseCron(cronExpr);
  const next = new Date(from);
  next.setSeconds(0, 0);
  next.setMinutes(next.getMinutes() + 1); // Start from at least next minute

  // Search up to 1 year ahead
  const maxIterations = 525960; // ~365.25 days * 24 hours * 60 minutes
  for (let i = 0; i < maxIterations; i++) {
    const month = next.getMonth() + 1; // 1-12
    const dom = next.getDate();
    const dow = next.getDay(); // 0 = Sunday
    const hour = next.getHours();
    const minute = next.getMinutes();

    if (
      cron.months.includes(month) &&
      cron.daysOfMonth.includes(dom) &&
      cron.daysOfWeek.includes(dow) &&
      cron.hours.includes(hour) &&
      cron.minutes.includes(minute)
    ) {
      return next;
    }

    next.setMinutes(next.getMinutes() + 1);
  }

  throw new Error(`Could not find next run time for cron: ${cronExpr}`);
}

// ── Config Persistence ──────────────────────────────────────────────

function loadConfig() {
  if (_config !== null) return _config;
  ensureDir();

  if (fs.existsSync(SCHEDULER_PATH)) {
    try {
      const raw = fs.readFileSync(SCHEDULER_PATH, 'utf-8');
      _config = JSON.parse(raw);
    } catch (err) {
      console.error('[scheduler] Corrupt config, starting fresh:', err.message);
      _config = { jobs: {} };
    }
  } else {
    _config = { jobs: {} };
  }

  return _config;
}

function saveConfig() {
  ensureDir();
  const config = loadConfig();
  const tmp = SCHEDULER_PATH + '.tmp';
  try {
    fs.writeFileSync(tmp, JSON.stringify(config, null, 2), 'utf-8');
    fs.renameSync(tmp, SCHEDULER_PATH);
  } catch (err) {
    console.error('[scheduler] Config save failed:', err.message);
    try { fs.unlinkSync(tmp); } catch (_) {}
    throw err;
  }
}

// ── Job Execution ───────────────────────────────────────────────────

/**
 * Execute a job (stub — actual agent execution to be wired later)
 * @param {object} job
 */
async function executeJob(job) {
  const startTime = Date.now();
  console.log(`[scheduler] Running job: ${job.name} (${job.id})`);

  try {
    // TODO: Wire to actual agent execution
    // For now, log the execution intent
    const output = `Executed ${job.agentName || 'default'} agent with input: ${job.input || '(none)'}`;
    const durationMs = Date.now() - startTime;

    jobs.recordRun(job.id, {
      status: 'success',
      output,
      durationMs,
    });

    console.log(`[scheduler] Job ${job.name} completed in ${durationMs}ms`);

    // Reschedule if cron-based
    if (job.schedule && !job.intervalMs) {
      scheduleNext(job);
    }
  } catch (err) {
    const durationMs = Date.now() - startTime;
    jobs.recordRun(job.id, {
      status: 'error',
      error: err.message,
      durationMs,
    });
    console.error(`[scheduler] Job ${job.name} failed:`, err.message);

    // Still reschedule on error
    if (job.schedule && !job.intervalMs) {
      scheduleNext(job);
    }
  }
}

/**
 * Schedule the next run of a cron job
 */
function scheduleNext(job) {
  // Clear existing timer
  if (_timers.has(job.id)) {
    clearTimeout(_timers.get(job.id));
    _timers.delete(job.id);
  }

  if (!job.enabled) return;

  try {
    if (job.schedule) {
      const nextRun = getNextCronTime(job.schedule);
      const delayMs = nextRun.getTime() - Date.now();

      // Update next run time in config
      const config = loadConfig();
      if (config.jobs[job.id]) {
        config.jobs[job.id].nextRun = nextRun.toISOString();
        saveConfig();
      }

      if (delayMs > 0) {
        // Node.js setTimeout max is ~24.8 days (2^31 - 1 ms)
        // For longer delays, use a chain
        const MAX_TIMEOUT = 2147483647;
        if (delayMs <= MAX_TIMEOUT) {
          const timer = setTimeout(() => {
            _timers.delete(job.id);
            executeJob(job);
          }, delayMs);
          timer.unref(); // Don't keep process alive just for this
          _timers.set(job.id, timer);
        } else {
          // Recheduler will catch up on next startup
          console.log(`[scheduler] Job ${job.name} next run too far out (${Math.round(delayMs / 86400000)}d), will schedule on restart`);
        }
      }
    } else if (job.intervalMs) {
      const timer = setInterval(() => {
        executeJob(job);
      }, job.intervalMs);
      timer.unref();
      _timers.set(job.id, timer);
    }
  } catch (err) {
    console.error(`[scheduler] Failed to schedule ${job.name}:`, err.message);
  }
}

// ── Public API ──────────────────────────────────────────────────────

/**
 * Add a new scheduled job
 * @param {object} config
 * @param {string} config.name - Human-readable name
 * @param {string} config.agentName - Agent to run
 * @param {string} config.input - Input/prompt for the agent
 * @param {string} config.schedule - Cron expression (min hour dom month dow)
 * @param {number} config.intervalMs - Alternative: interval in milliseconds
 * @param {boolean} config.enabled - Whether job is active (default: true)
 * @returns {object} The created job
 */
function addJob(config) {
  const cfg = loadConfig();
  const id = generateId();

  // Validate cron if provided
  if (config.schedule) {
    parseCron(config.schedule); // throws on invalid
  }

  if (!config.schedule && !config.intervalMs) {
    throw new Error('Job must have either a schedule (cron) or intervalMs');
  }

  const job = {
    id,
    name: config.name || 'Unnamed Job',
    agentName: config.agentName || 'default',
    input: config.input || '',
    schedule: config.schedule || null,
    intervalMs: config.intervalMs || null,
    enabled: config.enabled !== false,
    createdAt: new Date().toISOString(),
    nextRun: null,
  };

  // Calculate next run
  if (job.schedule) {
    try {
      job.nextRun = getNextCronTime(job.schedule).toISOString();
    } catch (_) {}
  }

  cfg.jobs[id] = job;
  saveConfig();

  // Start scheduling if scheduler is running
  if (_running) {
    scheduleNext(job);
  }

  return job;
}

/**
 * Remove a job
 * @param {string} id
 * @returns {boolean}
 */
function removeJob(id) {
  const cfg = loadConfig();
  if (!cfg.jobs[id]) return false;

  // Clear timer
  if (_timers.has(id)) {
    const timer = _timers.get(id);
    clearTimeout(timer);
    clearInterval(timer);
    _timers.delete(id);
  }

  delete cfg.jobs[id];
  saveConfig();

  // Clean up history
  jobs.clearJobHistory(id);
  return true;
}

/**
 * List all jobs
 * @returns {object[]}
 */
function listJobs() {
  const cfg = loadConfig();
  return Object.values(cfg.jobs).map(job => ({
    ...job,
    lastRun: jobs.getLastRun(job.id),
    stats: jobs.getJobStats(job.id),
  }));
}

/**
 * Enable a job
 * @param {string} id
 * @returns {boolean}
 */
function enableJob(id) {
  const cfg = loadConfig();
  const job = cfg.jobs[id];
  if (!job) return false;

  job.enabled = true;
  saveConfig();

  if (_running) scheduleNext(job);
  return true;
}

/**
 * Disable a job
 * @param {string} id
 * @returns {boolean}
 */
function disableJob(id) {
  const cfg = loadConfig();
  const job = cfg.jobs[id];
  if (!job) return false;

  job.enabled = false;
  saveConfig();

  // Clear timer
  if (_timers.has(id)) {
    const timer = _timers.get(id);
    clearTimeout(timer);
    clearInterval(timer);
    _timers.delete(id);
  }

  return true;
}

/**
 * Get when a job will next run
 * @param {string} id
 * @returns {object|null} { nextRun: ISO string, inMs: number, inHuman: string }
 */
function getNextRun(id) {
  const cfg = loadConfig();
  const job = cfg.jobs[id];
  if (!job) return null;

  if (!job.enabled) return { nextRun: null, disabled: true };

  if (job.schedule) {
    try {
      const next = getNextCronTime(job.schedule);
      const inMs = next.getTime() - Date.now();
      return {
        nextRun: next.toISOString(),
        inMs,
        inHuman: humanDuration(inMs),
      };
    } catch (_) {
      return { nextRun: null, error: 'Invalid cron expression' };
    }
  }

  if (job.intervalMs) {
    return {
      interval: job.intervalMs,
      inHuman: `Every ${humanDuration(job.intervalMs)}`,
    };
  }

  return null;
}

/**
 * Start the scheduler — activate all enabled jobs
 */
function start() {
  if (_running) return;
  _running = true;

  const cfg = loadConfig();
  for (const job of Object.values(cfg.jobs)) {
    if (job.enabled) {
      scheduleNext(job);
    }
  }

  console.log(`[scheduler] Started with ${Object.keys(cfg.jobs).length} jobs`);
}

/**
 * Stop the scheduler — clear all timers
 */
function stop() {
  _running = false;
  for (const [id, timer] of _timers) {
    clearTimeout(timer);
    clearInterval(timer);
  }
  _timers.clear();
  console.log('[scheduler] Stopped');
}

/**
 * Reload config from disk and restart
 */
function reload() {
  stop();
  _config = null;
  start();
}

// ── Utility ─────────────────────────────────────────────────────────

function humanDuration(ms) {
  if (ms < 0) return 'now';
  const seconds = Math.floor(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const remainMinutes = minutes % 60;
  if (hours < 24) return `${hours}h ${remainMinutes}m`;
  const days = Math.floor(hours / 24);
  return `${days}d ${hours % 24}h`;
}

module.exports = {
  addJob,
  removeJob,
  listJobs,
  enableJob,
  disableJob,
  getNextRun,
  start,
  stop,
  reload,

  // Cron utilities (exported for testing & CLI)
  parseCron,
  getNextCronTime,

  // Re-export jobs module
  jobs,

  SCHEDULER_PATH,
};
