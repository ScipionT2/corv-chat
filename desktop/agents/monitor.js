/**
 * @module monitor
 * @description Monitor Agent — continuous agent that runs on interval.
 * Has persistent memory via SQLite, compresses old observations,
 * and alerts on significant changes.
 */

'use strict';

const BaseAgent = require('./base-agent');
const fs = require('fs');
const path = require('path');

/** Default check interval: 5 minutes */
const DEFAULT_INTERVAL = 5 * 60 * 1000;

/** Max observations before compression */
const COMPRESS_THRESHOLD = 50;

/** Max observations to keep after compression */
const COMPRESSED_KEEP = 10;

const MONITOR_SYSTEM = `You are Nova Monitor Agent. You continuously observe a data source and detect significant changes.

## Your Role
- Run periodic checks on the monitored target
- Compare current state with previous observations
- Detect anomalies, trends, and significant changes
- Alert the user when something important happens

## Response Format
After each observation, respond with:
\`\`\`json
{
  "summary": "Brief description of current state",
  "changed": true/false,
  "severity": "info" | "warning" | "critical",
  "details": "Detailed analysis if changed",
  "alert": "Message to send to user (only if severity is warning or critical)"
}
\`\`\`
`;

/**
 * @typedef {Object} Observation
 * @property {number} timestamp
 * @property {string} summary
 * @property {boolean} changed
 * @property {'info'|'warning'|'critical'} severity
 * @property {string} [details]
 * @property {string} [alert]
 * @property {string} [rawData]
 */

/**
 * Continuous monitoring agent with persistent memory.
 *
 * @extends BaseAgent
 */
class MonitorAgent extends BaseAgent {
  /**
   * @param {Object} [config={}]
   * @param {number} [config.interval=300000] - Check interval in ms
   * @param {string} [config.target] - What to monitor (description)
   * @param {Function} [config.checkFn] - Custom check function returning data
   * @param {string} [config.storePath] - Path to persist observations
   * @param {number} [config.compressThreshold=50]
   */
  constructor(config = {}) {
    super('monitor', 'continuous', {
      systemPrompt: MONITOR_SYSTEM,
      temperature: 0.2,
      ...config,
    });

    /** @type {number} */
    this.interval = config.interval || DEFAULT_INTERVAL;

    /** @type {string} */
    this.target = config.target || 'unknown';

    /** @type {Function|null} */
    this.checkFn = config.checkFn || null;

    /** @type {string} */
    this.storePath = config.storePath || '';

    /** @type {number} */
    this.compressThreshold = config.compressThreshold || COMPRESS_THRESHOLD;

    /** @type {Observation[]} */
    this.observations = [];

    /** @type {NodeJS.Timeout|null} */
    this._timer = null;

    /** @type {number} */
    this._checkCount = 0;

    // Load persisted observations
    this._loadStore();
  }

  /**
   * Start the monitoring loop.
   *
   * @param {string} input - Monitoring target description
   * @param {Object} [context={}]
   * @param {Function} [context.checkFn] - Override check function
   * @param {Function} [context.onAlert] - Callback for alerts
   * @returns {Promise<import('./base-agent').AgentResult>}
   */
  async run(input, context = {}) {
    this._prepare();
    this.target = input || this.target;

    const checkFn = context.checkFn || this.checkFn;
    const onAlert = context.onAlert || ((alert) => this.emit('alert', alert));

    if (!checkFn) {
      return this._error(new Error('Monitor requires a checkFn to gather data'));
    }

    this._addStep('start', `Monitoring: ${this.target} (interval: ${this.interval}ms)`);
    this.emit('monitoring-started', { target: this.target, interval: this.interval });

    // Run first check immediately
    await this._check(checkFn, onAlert);

    // Set up interval
    this._timer = setInterval(async () => {
      if (this.status !== 'running') {
        this._stopTimer();
        return;
      }
      await this._check(checkFn, onAlert);
    }, this.interval);

    // Return immediately — monitor runs in background
    return this._result(`Monitor started for "${this.target}". Checking every ${this.interval / 1000}s.`, {
      target: this.target,
      interval: this.interval,
      observationCount: this.observations.length,
    });
  }

  /**
   * Perform a single check cycle.
   *
   * @param {Function} checkFn
   * @param {Function} onAlert
   * @private
   */
  async _check(checkFn, onAlert) {
    this._checkCount++;
    this._addStep('check', `Check #${this._checkCount}`);

    try {
      // Gather data
      const rawData = await checkFn();
      const dataStr = typeof rawData === 'string' ? rawData : JSON.stringify(rawData);

      // Build context with recent history
      const recentHistory = this.observations.slice(-5).map((o) =>
        `[${new Date(o.timestamp).toISOString()}] ${o.summary} (changed: ${o.changed}, severity: ${o.severity})`
      ).join('\n');

      const prompt = `## Current Check (#${this._checkCount}) for: ${this.target}

### Current Data:
${dataStr.slice(0, 3000)}

### Recent History:
${recentHistory || '(first check, no history)'}

Analyze the current state. Compare with recent history. Report any changes.`;

      const response = await this.think(prompt);
      const parsed = this._extractJSON(response);

      /** @type {Observation} */
      const observation = {
        timestamp: Date.now(),
        summary: parsed?.summary || response.slice(0, 200),
        changed: parsed?.changed || false,
        severity: parsed?.severity || 'info',
        details: parsed?.details || '',
        alert: parsed?.alert || '',
        rawData: dataStr.slice(0, 500),
      };

      this.observations.push(observation);
      this.emit('observation', observation);

      // Alert if needed
      if (observation.alert && (observation.severity === 'warning' || observation.severity === 'critical')) {
        onAlert({
          target: this.target,
          severity: observation.severity,
          message: observation.alert,
          timestamp: observation.timestamp,
          checkNumber: this._checkCount,
        });
      }

      // Compress if needed
      if (this.observations.length > this.compressThreshold) {
        await this._compress();
      }

      // Persist
      this._saveStore();

    } catch (err) {
      this._addStep('check-error', err.message);
      this.emit('check-error', err);
    }
  }

  /**
   * Compress old observations to save memory.
   * Keeps most recent observations and summarizes older ones.
   *
   * @private
   */
  async _compress() {
    if (this.observations.length <= COMPRESSED_KEEP) return;

    this._addStep('compress', `Compressing ${this.observations.length} observations`);

    const toCompress = this.observations.slice(0, -COMPRESSED_KEEP);
    const toKeep = this.observations.slice(-COMPRESSED_KEEP);

    // Generate summary of compressed observations
    const summaryText = toCompress.map((o) =>
      `[${new Date(o.timestamp).toISOString()}] ${o.summary} (severity: ${o.severity})`
    ).join('\n');

    try {
      const summary = await this.think(
        `Summarize these monitoring observations into a brief history:\n${summaryText}`
      );

      // Replace with a single compressed observation
      const compressed = {
        timestamp: toCompress[0].timestamp,
        summary: `[COMPRESSED: ${toCompress.length} observations] ${summary.slice(0, 500)}`,
        changed: false,
        severity: 'info',
        details: '',
      };

      this.observations = [compressed, ...toKeep];
      this._addStep('compressed', `Reduced to ${this.observations.length} observations`);
    } catch (err) {
      // If compression fails, just trim
      this.observations = toKeep;
    }
  }

  /**
   * Stop the monitor.
   */
  stop() {
    this._stopTimer();
    this._saveStore();
    super.stop();
  }

  /**
   * Clear the interval timer.
   * @private
   */
  _stopTimer() {
    if (this._timer) {
      clearInterval(this._timer);
      this._timer = null;
    }
  }

  /**
   * Get state including observations.
   *
   * @returns {Object}
   */
  getState() {
    return {
      ...super.getState(),
      target: this.target,
      interval: this.interval,
      observations: this.observations,
      checkCount: this._checkCount,
    };
  }

  /**
   * Restore state including observations.
   *
   * @param {Object} state
   */
  setState(state) {
    super.setState(state);
    if (state.target) this.target = state.target;
    if (state.interval) this.interval = state.interval;
    if (state.observations) this.observations = state.observations;
    if (state.checkCount) this._checkCount = state.checkCount;
  }

  /**
   * Load observations from disk.
   * @private
   */
  _loadStore() {
    if (!this.storePath) return;
    try {
      if (fs.existsSync(this.storePath)) {
        const data = JSON.parse(fs.readFileSync(this.storePath, 'utf-8'));
        this.observations = data.observations || [];
        this._checkCount = data.checkCount || 0;
      }
    } catch (_) {
      // Fresh start
    }
  }

  /**
   * Persist observations to disk.
   * @private
   */
  _saveStore() {
    if (!this.storePath) return;
    try {
      const dir = path.dirname(this.storePath);
      if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
      }
      fs.writeFileSync(this.storePath, JSON.stringify({
        target: this.target,
        observations: this.observations,
        checkCount: this._checkCount,
        lastSaved: Date.now(),
      }, null, 2), 'utf-8');
    } catch (_) {
      // Best effort
    }
  }
}

/** @type {string} */
MonitorAgent.agentType = 'monitor';

/** @type {string} */
MonitorAgent.description = 'Continuous monitoring agent with persistent memory, compression, and alerts';

/** @type {string} */
MonitorAgent.category = 'continuous';

module.exports = MonitorAgent;
