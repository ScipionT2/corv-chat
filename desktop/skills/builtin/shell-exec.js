'use strict';

const { execSync } = require('child_process');
const { BaseSkill } = require('../base-skill');

/**
 * Dangerous command patterns that are always blocked.
 * @type {string[]}
 */
const BLOCKLIST = [
  'rm -rf /',
  'rm -rf ~',
  'mkfs',
  'dd if=',
  ':(){ :',
  'sudo rm',
];

/** Maximum output size in bytes (10KB) */
const MAX_OUTPUT = 10 * 1024;

/** Default command timeout in milliseconds (30s) */
const DEFAULT_TIMEOUT = 30000;

/**
 * ShellExecSkill — executes shell commands with safety guardrails.
 * Blocks known destructive patterns and enforces output/timeout limits.
 */
class ShellExecSkill extends BaseSkill {
  constructor() {
    super({
      name: 'shell_exec',
      description: 'Execute a shell command and return its output. Blocks dangerous commands. Max output 10KB, default timeout 30s.',
      version: '1.0.0',
      category: 'system',
      parameters: {
        type: 'object',
        properties: {
          command: {
            type: 'string',
            description: 'The shell command to execute',
          },
          timeout: {
            type: 'number',
            description: 'Timeout in milliseconds (default: 30000, max: 120000)',
          },
          cwd: {
            type: 'string',
            description: 'Working directory for the command',
          },
        },
        required: ['command'],
      },
    });
  }

  /**
   * Check if a command matches any blocked pattern.
   * @param {string} command
   * @returns {string|null} The matched pattern, or null if safe.
   * @private
   */
  _checkBlocklist(command) {
    const normalized = command.toLowerCase().replace(/\s+/g, ' ').trim();
    for (const pattern of BLOCKLIST) {
      if (normalized.includes(pattern)) {
        return pattern;
      }
    }
    return null;
  }

  /**
   * Execute the shell command.
   * @param {{command: string, timeout?: number, cwd?: string}} args
   * @returns {Promise<{stdout: string, exitCode: number, truncated: boolean}>}
   */
  async execute(args) {
    if (!args || !args.command || typeof args.command !== 'string') {
      throw new Error('Parameter "command" is required and must be a string');
    }

    const command = args.command.trim();
    if (!command) {
      throw new Error('Command cannot be empty');
    }

    // Safety check
    const blocked = this._checkBlocklist(command);
    if (blocked) {
      throw new Error(`Command blocked: contains dangerous pattern "${blocked}"`);
    }

    // Enforce timeout bounds
    let timeout = args.timeout || DEFAULT_TIMEOUT;
    if (typeof timeout !== 'number' || timeout < 0) {
      timeout = DEFAULT_TIMEOUT;
    }
    timeout = Math.min(timeout, 120000); // cap at 2 minutes

    const opts = {
      timeout,
      maxBuffer: MAX_OUTPUT + 1024, // slight over-allocation for detection
      encoding: 'utf-8',
      stdio: ['pipe', 'pipe', 'pipe'],
    };

    if (args.cwd) {
      opts.cwd = args.cwd;
    }

    try {
      let stdout = execSync(command, opts);
      let truncated = false;

      if (stdout && stdout.length > MAX_OUTPUT) {
        stdout = stdout.slice(0, MAX_OUTPUT) + '\n... [output truncated at 10KB]';
        truncated = true;
      }

      return { stdout: stdout || '', exitCode: 0, truncated };
    } catch (err) {
      // execSync throws on non-zero exit
      let stdout = (err.stdout || '').toString();
      let stderr = (err.stderr || '').toString();
      let truncated = false;

      if (stdout.length > MAX_OUTPUT) {
        stdout = stdout.slice(0, MAX_OUTPUT) + '\n... [output truncated at 10KB]';
        truncated = true;
      }
      if (stderr.length > MAX_OUTPUT) {
        stderr = stderr.slice(0, MAX_OUTPUT) + '\n... [output truncated at 10KB]';
        truncated = true;
      }

      if (err.killed) {
        throw new Error(`Command timed out after ${timeout}ms`);
      }

      return {
        stdout,
        stderr,
        exitCode: err.status || 1,
        truncated,
      };
    }
  }
}

module.exports = new ShellExecSkill();
