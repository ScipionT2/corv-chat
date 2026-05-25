/**
 * @module code-agent
 * @description Code Execution Agent (CodeAct) — generates and executes code.
 * Generates Python or JavaScript, runs in a sandboxed child process,
 * captures output, and iterates on errors.
 */

'use strict';

const BaseAgent = require('./base-agent');
const { execFile } = require('child_process');
const fs = require('fs');
const path = require('path');
const os = require('os');

const MAX_RETRIES = 3;
const EXEC_TIMEOUT = 30_000; // 30 seconds
const MAX_OUTPUT = 10_000; // chars

/**
 * Dangerous patterns blocked from execution.
 * @type {RegExp[]}
 */
const BLOCKED_PATTERNS = [
  /rm\s+(-rf?\s+)?[\/~]/i,       // rm -rf / or ~
  /rmdir\s+/i,                     // rmdir
  /format\s+/i,                    // format
  /del\s+\/[sfq]/i,               // Windows del
  />\s*\/dev\/sd/i,               // Write to devices
  /mkfs/i,                         // Format filesystem
  /dd\s+if=/i,                     // dd command
  /:(){ :|:& };:/,                // Fork bomb
  /import\s+subprocess/i,          // Python subprocess (network escape)
  /child_process/i,                // Node child_process (escape)
  /require\s*\(\s*['"](?:child_process|cluster|dgram|net|tls|http|https)['"]\s*\)/i,
  /eval\s*\(/i,                    // eval()
];

/**
 * Patterns that require explicit network permission.
 * @type {RegExp[]}
 */
const NETWORK_PATTERNS = [
  /fetch\s*\(/i,
  /https?:\/\//i,
  /import\s+(?:urllib|requests|aiohttp|httpx)/i,
  /socket\./i,
  /XMLHttpRequest/i,
];

const CODE_SYSTEM = `You are Nova Code Agent. You solve tasks by writing and executing code.

## Rules
- Write clean, complete, runnable code
- Prefer Python for data tasks, JavaScript for Node.js tasks
- Always print results to stdout
- Handle errors gracefully in your code
- Do NOT use network access unless explicitly permitted
- Do NOT modify files outside the temp directory
- Do NOT use subprocess, child_process, or eval

## Response Format
Respond with a JSON object:

\`\`\`json
{
  "language": "python" or "javascript",
  "code": "your complete code here",
  "explanation": "brief explanation of what the code does"
}
\`\`\`

If execution fails, you'll receive the error. Fix the code and try again.
When you have the final result, respond with:
\`\`\`json
{"tool": "final_answer", "args": {"answer": "result from code execution"}}
\`\`\`
`;

/**
 * Code execution agent that generates and runs Python/JavaScript.
 *
 * @extends BaseAgent
 */
class CodeAgent extends BaseAgent {
  /**
   * @param {Object} [config={}]
   * @param {number} [config.maxRetries=3] - Max retry attempts on error
   * @param {number} [config.execTimeout=30000] - Execution timeout in ms
   * @param {boolean} [config.allowNetwork=false] - Allow network in code
   * @param {string} [config.workDir] - Override temp directory
   */
  constructor(config = {}) {
    super('code', 'on-demand', {
      systemPrompt: CODE_SYSTEM,
      temperature: 0.2,
      ...config,
    });

    /** @type {number} */
    this.maxRetries = config.maxRetries || MAX_RETRIES;

    /** @type {number} */
    this.execTimeout = config.execTimeout || EXEC_TIMEOUT;

    /** @type {boolean} */
    this.allowNetwork = config.allowNetwork || false;

    /** @type {string} */
    this.workDir = config.workDir || '';

    /** @type {Array<{language: string, code: string, stdout: string, stderr: string, exitCode: number}>} */
    this.executions = [];
  }

  /**
   * Run the code agent.
   *
   * @param {string} input - Task description
   * @param {Object} [context={}]
   * @param {boolean} [context.allowNetwork] - Override network permission
   * @returns {Promise<import('./base-agent').AgentResult>}
   */
  async run(input, context = {}) {
    this._prepare();
    this.executions = [];

    const allowNet = context.allowNetwork ?? this.allowNetwork;

    // Create temp directory for this run
    const tmpDir = this.workDir || fs.mkdtempSync(path.join(os.tmpdir(), 'nova-code-'));

    try {
      this._messages = [];
      let currentInput = input;
      let finalAnswer = null;
      let attempt = 0;

      while (attempt <= this.maxRetries && this.status === 'running') {
        attempt++;
        this._addStep('generate', `Generating code (attempt ${attempt}/${this.maxRetries + 1})`);

        const response = await this.think(currentInput);
        const parsed = this._extractJSON(response);

        if (!parsed) {
          finalAnswer = response;
          break;
        }

        // Check for final answer
        if (parsed.tool === 'final_answer') {
          finalAnswer = parsed.args?.answer || response;
          break;
        }

        // Must have language and code
        if (!parsed.language || !parsed.code) {
          currentInput = 'Your response must include "language" (python or javascript) and "code" fields. Try again.';
          continue;
        }

        const { language, code, explanation } = parsed;

        if (explanation) {
          this._addStep('explanation', explanation.slice(0, 200));
        }

        // Safety check
        const safetyResult = this._checkSafety(code, allowNet);
        if (!safetyResult.safe) {
          this._addStep('blocked', `Unsafe code: ${safetyResult.reason}`);
          currentInput = `Your code was blocked for safety: ${safetyResult.reason}. Rewrite without dangerous operations.`;
          continue;
        }

        // Execute the code
        this._addStep('execute', `Running ${language} code (${code.length} chars)`);
        const result = await this._execute(language, code, tmpDir);

        this.executions.push({
          language,
          code,
          stdout: result.stdout,
          stderr: result.stderr,
          exitCode: result.exitCode,
        });

        if (result.exitCode === 0) {
          // Success
          const output = result.stdout.trim() || '(no output)';
          this._addStep('output', output.slice(0, 500));
          this.emit('execution', { success: true, output });

          // Ask LLM to interpret the result
          currentInput = `Code executed successfully.\n\nOutput:\n${output}\n\nBased on this output, provide the final answer using:\n\`\`\`json\n{"tool": "final_answer", "args": {"answer": "your interpretation"}}\n\`\`\``;
        } else {
          // Error — retry
          const errorMsg = (result.stderr || result.stdout || 'Unknown error').slice(0, 1000);
          this._addStep('error', `Exit ${result.exitCode}: ${errorMsg.slice(0, 200)}`);
          this.emit('execution', { success: false, error: errorMsg });

          if (attempt > this.maxRetries) {
            finalAnswer = `Code execution failed after ${this.maxRetries} retries.\nLast error: ${errorMsg}`;
            break;
          }

          currentInput = `Code execution failed with error:\n${errorMsg}\n\nFix the code and try again. Attempt ${attempt + 1}/${this.maxRetries + 1}.`;
        }
      }

      if (!finalAnswer) {
        finalAnswer = 'Code execution completed but no explicit result was produced.';
      }

      return this._result(finalAnswer, {
        executions: this.executions.length,
        attempts: attempt,
        languages: [...new Set(this.executions.map((e) => e.language))],
        workDir: tmpDir,
      });
    } catch (err) {
      return this._error(err);
    } finally {
      // Clean up temp dir (best effort)
      this._cleanupTmpDir(tmpDir);
      this._cleanup();
    }
  }

  /**
   * Check code for safety violations.
   *
   * @param {string} code
   * @param {boolean} allowNetwork
   * @returns {{ safe: boolean, reason?: string }}
   * @private
   */
  _checkSafety(code, allowNetwork) {
    for (const pattern of BLOCKED_PATTERNS) {
      if (pattern.test(code)) {
        return { safe: false, reason: `Blocked pattern: ${pattern.source}` };
      }
    }

    if (!allowNetwork) {
      for (const pattern of NETWORK_PATTERNS) {
        if (pattern.test(code)) {
          return { safe: false, reason: `Network access not allowed: ${pattern.source}` };
        }
      }
    }

    return { safe: true };
  }

  /**
   * Execute code in a sandboxed child process.
   *
   * @param {'python'|'javascript'} language
   * @param {string} code
   * @param {string} tmpDir
   * @returns {Promise<{stdout: string, stderr: string, exitCode: number}>}
   * @private
   */
  _execute(language, code, tmpDir) {
    return new Promise((resolve) => {
      let ext, binary;

      if (language === 'python') {
        ext = '.py';
        binary = 'python3';
      } else if (language === 'javascript') {
        ext = '.js';
        binary = 'node';
      } else {
        resolve({ stdout: '', stderr: `Unsupported language: ${language}`, exitCode: 1 });
        return;
      }

      const filename = `nova_exec_${Date.now()}${ext}`;
      const filepath = path.join(tmpDir, filename);

      try {
        fs.writeFileSync(filepath, code, 'utf-8');
      } catch (err) {
        resolve({ stdout: '', stderr: `Failed to write code file: ${err.message}`, exitCode: 1 });
        return;
      }

      const options = {
        timeout: this.execTimeout,
        maxBuffer: MAX_OUTPUT * 2,
        cwd: tmpDir,
        env: {
          ...process.env,
          HOME: tmpDir,
          TMPDIR: tmpDir,
        },
      };

      execFile(binary, [filepath], options, (error, stdout, stderr) => {
        // Clean up script file
        try { fs.unlinkSync(filepath); } catch (_) {}

        const exitCode = error ? (error.code || 1) : 0;

        resolve({
          stdout: (stdout || '').slice(0, MAX_OUTPUT),
          stderr: (stderr || '').slice(0, MAX_OUTPUT),
          exitCode: typeof exitCode === 'number' ? exitCode : 1,
        });
      });
    });
  }

  /**
   * Remove temp directory (best effort).
   *
   * @param {string} tmpDir
   * @private
   */
  _cleanupTmpDir(tmpDir) {
    if (!tmpDir || !tmpDir.includes('nova-code-')) return;
    try {
      const files = fs.readdirSync(tmpDir);
      for (const file of files) {
        try { fs.unlinkSync(path.join(tmpDir, file)); } catch (_) {}
      }
      fs.rmdirSync(tmpDir);
    } catch (_) {
      // Best effort cleanup
    }
  }
}

/** @type {string} */
CodeAgent.agentType = 'code';

/** @type {string} */
CodeAgent.description = 'Code execution agent — generates and runs Python/JavaScript with safety sandboxing';

/** @type {string} */
CodeAgent.category = 'on-demand';

module.exports = CodeAgent;
