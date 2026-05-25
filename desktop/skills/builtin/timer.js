'use strict';

const fs = require('fs');
const path = require('path');
const os = require('os');
const { BaseSkill } = require('../base-skill');

/** Path to persist active timers */
const TIMERS_FILE = path.join(os.homedir(), '.nova', 'timers.json');

/**
 * Load timers from disk.
 * @returns {Object<string, {id: string, label: string, durationMs: number, createdAt: string, expiresAt: string}>}
 */
function loadTimers() {
  try {
    if (fs.existsSync(TIMERS_FILE)) {
      const raw = fs.readFileSync(TIMERS_FILE, 'utf-8');
      return JSON.parse(raw);
    }
  } catch {
    // Corrupted file — start fresh
  }
  return {};
}

/**
 * Save timers to disk.
 * @param {object} timers
 */
function saveTimers(timers) {
  const dir = path.dirname(TIMERS_FILE);
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
  fs.writeFileSync(TIMERS_FILE, JSON.stringify(timers, null, 2), 'utf-8');
}

/**
 * Generate a short unique ID.
 * @returns {string}
 */
function generateId() {
  return Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 7);
}

// ─── SetTimerSkill ───────────────────────────────────────────────────────────

/**
 * SetTimerSkill — creates a new timer with a label and duration.
 * Persists to ~/.nova/timers.json.
 */
class SetTimerSkill extends BaseSkill {
  constructor() {
    super({
      name: 'set_timer',
      description: 'Set a named timer with a duration in seconds. Timer is persisted to disk.',
      version: '1.0.0',
      category: 'utility',
      parameters: {
        type: 'object',
        properties: {
          label: {
            type: 'string',
            description: 'A human-readable label for the timer (e.g. "Pasta boiling")',
          },
          seconds: {
            type: 'number',
            description: 'Duration in seconds',
          },
        },
        required: ['label', 'seconds'],
      },
    });
  }

  /**
   * @param {{label: string, seconds: number}} args
   * @returns {Promise<{id: string, label: string, expiresAt: string}>}
   */
  async execute(args) {
    if (!args || !args.label || typeof args.label !== 'string') {
      throw new Error('Parameter "label" is required and must be a string');
    }
    if (!args.seconds || typeof args.seconds !== 'number' || args.seconds <= 0) {
      throw new Error('Parameter "seconds" is required and must be a positive number');
    }
    if (args.seconds > 86400) {
      throw new Error('Maximum timer duration is 24 hours (86400 seconds)');
    }

    const timers = loadTimers();
    const id = generateId();
    const now = new Date();
    const expiresAt = new Date(now.getTime() + args.seconds * 1000);

    timers[id] = {
      id,
      label: args.label.trim(),
      durationMs: args.seconds * 1000,
      createdAt: now.toISOString(),
      expiresAt: expiresAt.toISOString(),
    };

    saveTimers(timers);

    return {
      id,
      label: args.label.trim(),
      expiresAt: expiresAt.toISOString(),
      message: `Timer "${args.label.trim()}" set for ${args.seconds}s`,
    };
  }
}

// ─── ListTimersSkill ─────────────────────────────────────────────────────────

/**
 * ListTimersSkill — lists all active timers with remaining time.
 */
class ListTimersSkill extends BaseSkill {
  constructor() {
    super({
      name: 'list_timers',
      description: 'List all active timers with remaining time.',
      version: '1.0.0',
      category: 'utility',
      parameters: {
        type: 'object',
        properties: {},
      },
    });
  }

  /**
   * @returns {Promise<{timers: object[], expired: object[]}>}
   */
  async execute() {
    const timers = loadTimers();
    const now = Date.now();
    const active = [];
    const expired = [];

    for (const [id, timer] of Object.entries(timers)) {
      const expiresMs = new Date(timer.expiresAt).getTime();
      const remainingMs = expiresMs - now;

      if (remainingMs <= 0) {
        expired.push({
          id,
          label: timer.label,
          expiredAgo: `${Math.round(-remainingMs / 1000)}s ago`,
        });
      } else {
        const remainingSec = Math.ceil(remainingMs / 1000);
        const mins = Math.floor(remainingSec / 60);
        const secs = remainingSec % 60;
        active.push({
          id,
          label: timer.label,
          remaining: mins > 0 ? `${mins}m ${secs}s` : `${secs}s`,
          remainingSeconds: remainingSec,
          expiresAt: timer.expiresAt,
        });
      }
    }

    return { active, expired, total: active.length + expired.length };
  }
}

// ─── CancelTimerSkill ────────────────────────────────────────────────────────

/**
 * CancelTimerSkill — cancels an active timer by ID or label.
 */
class CancelTimerSkill extends BaseSkill {
  constructor() {
    super({
      name: 'cancel_timer',
      description: 'Cancel an active timer by its ID or label.',
      version: '1.0.0',
      category: 'utility',
      parameters: {
        type: 'object',
        properties: {
          id: {
            type: 'string',
            description: 'Timer ID to cancel',
          },
          label: {
            type: 'string',
            description: 'Timer label to cancel (matches first found)',
          },
        },
      },
    });
  }

  /**
   * @param {{id?: string, label?: string}} args
   * @returns {Promise<{cancelled: boolean, id: string, label: string}>}
   */
  async execute(args) {
    if ((!args || (!args.id && !args.label))) {
      throw new Error('Either "id" or "label" must be provided');
    }

    const timers = loadTimers();
    let targetId = null;
    let targetLabel = null;

    if (args.id && timers[args.id]) {
      targetId = args.id;
      targetLabel = timers[args.id].label;
    } else if (args.label) {
      // Find by label (first match)
      for (const [id, timer] of Object.entries(timers)) {
        if (timer.label.toLowerCase() === args.label.toLowerCase()) {
          targetId = id;
          targetLabel = timer.label;
          break;
        }
      }
    }

    if (!targetId) {
      throw new Error(`Timer not found: ${args.id || args.label}`);
    }

    delete timers[targetId];
    saveTimers(timers);

    return {
      cancelled: true,
      id: targetId,
      label: targetLabel,
      message: `Timer "${targetLabel}" cancelled`,
    };
  }
}

// Export all three skills
module.exports = {
  skills: [
    new SetTimerSkill(),
    new ListTimersSkill(),
    new CancelTimerSkill(),
  ],
};
