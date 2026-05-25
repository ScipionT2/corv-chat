'use strict';

/**
 * Nova Job Persistence & History
 * 
 * Stores job run history (last 50 runs per job).
 * Persists to ~/.nova/scheduler-history.json
 */

const fs = require('fs');
const path = require('path');
const os = require('os');

const NOVA_DIR = path.join(os.homedir(), '.nova');
const HISTORY_PATH = path.join(NOVA_DIR, 'scheduler-history.json');
const MAX_RUNS_PER_JOB = 50;

let _history = null;

// ── Helpers ─────────────────────────────────────────────────────────

function ensureDir() {
  if (!fs.existsSync(NOVA_DIR)) {
    fs.mkdirSync(NOVA_DIR, { recursive: true });
  }
}

// ── Load / Save ─────────────────────────────────────────────────────

function load() {
  if (_history !== null) return _history;
  ensureDir();

  if (fs.existsSync(HISTORY_PATH)) {
    try {
      const raw = fs.readFileSync(HISTORY_PATH, 'utf-8');
      _history = JSON.parse(raw);
    } catch (err) {
      console.error('[scheduler/jobs] Corrupt history, starting fresh:', err.message);
      _history = {};
    }
  } else {
    _history = {};
  }

  return _history;
}

function save() {
  ensureDir();
  const history = load();
  const tmp = HISTORY_PATH + '.tmp';
  try {
    fs.writeFileSync(tmp, JSON.stringify(history, null, 2), 'utf-8');
    fs.renameSync(tmp, HISTORY_PATH);
  } catch (err) {
    console.error('[scheduler/jobs] Save failed:', err.message);
    try { fs.unlinkSync(tmp); } catch (_) {}
    throw err;
  }
}

// ── History Operations ──────────────────────────────────────────────

/**
 * Record a job run
 * @param {string} jobId
 * @param {object} run - { status: 'success'|'error', output?: string, durationMs?: number }
 */
function recordRun(jobId, run) {
  const history = load();

  if (!history[jobId]) {
    history[jobId] = [];
  }

  const record = {
    timestamp: new Date().toISOString(),
    status: run.status || 'success',
    durationMs: run.durationMs || 0,
    output: run.output ? String(run.output).slice(0, 500) : null,
    error: run.error ? String(run.error).slice(0, 500) : null,
  };

  history[jobId].push(record);

  // Trim to max runs
  if (history[jobId].length > MAX_RUNS_PER_JOB) {
    history[jobId] = history[jobId].slice(-MAX_RUNS_PER_JOB);
  }

  save();
  return record;
}

/**
 * Get run history for a job
 * @param {string} jobId
 * @param {number} limit - Max runs to return (default: all)
 * @returns {object[]}
 */
function getHistory(jobId, limit) {
  const history = load();
  const runs = history[jobId] || [];
  if (limit) return runs.slice(-limit);
  return runs;
}

/**
 * Get the last run for a job
 * @param {string} jobId
 * @returns {object|null}
 */
function getLastRun(jobId) {
  const history = load();
  const runs = history[jobId] || [];
  return runs.length > 0 ? runs[runs.length - 1] : null;
}

/**
 * Get summary statistics for a job
 * @param {string} jobId
 * @returns {object}
 */
function getJobStats(jobId) {
  const history = load();
  const runs = history[jobId] || [];

  if (runs.length === 0) {
    return { totalRuns: 0, successCount: 0, errorCount: 0, avgDurationMs: 0 };
  }

  let successCount = 0;
  let errorCount = 0;
  let totalDuration = 0;

  for (const run of runs) {
    if (run.status === 'success') successCount++;
    else errorCount++;
    totalDuration += run.durationMs || 0;
  }

  return {
    totalRuns: runs.length,
    successCount,
    errorCount,
    successRate: ((successCount / runs.length) * 100).toFixed(1) + '%',
    avgDurationMs: Math.round(totalDuration / runs.length),
    lastRun: runs[runs.length - 1],
    firstRun: runs[0],
  };
}

/**
 * Clear history for a specific job
 * @param {string} jobId
 */
function clearJobHistory(jobId) {
  const history = load();
  delete history[jobId];
  save();
}

/**
 * Clear all history
 */
function clearAll() {
  _history = {};
  save();
}

/**
 * Get overview of all jobs with history
 * @returns {object} Map of jobId → { totalRuns, lastRun }
 */
function overview() {
  const history = load();
  const result = {};

  for (const [jobId, runs] of Object.entries(history)) {
    result[jobId] = {
      totalRuns: runs.length,
      lastRun: runs.length > 0 ? runs[runs.length - 1] : null,
    };
  }

  return result;
}

/**
 * Reload from disk
 */
function reload() {
  _history = null;
  load();
}

module.exports = {
  recordRun,
  getHistory,
  getLastRun,
  getJobStats,
  clearJobHistory,
  clearAll,
  overview,
  reload,
  HISTORY_PATH,
  MAX_RUNS_PER_JOB,
};
