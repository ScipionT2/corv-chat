/**
 * @module base-agent
 * @description Base Agent class for the Nova Agent Framework.
 * All agents extend this. Provides Ollama integration, tool execution,
 * state management, and streaming support.
 */

'use strict';

const http = require('http');
const { EventEmitter } = require('events');

// ── Defaults ────────────────────────────────────────────────────────
const OLLAMA_URL = 'http://localhost:11434';
const DEFAULT_MODEL = 'llama3.2:3b';
const DEFAULT_TEMPERATURE = 0.7;
const REQUEST_TIMEOUT = 120_000; // 2 minutes

/**
 * @typedef {Object} AgentResult
 * @property {string} output - Final text output
 * @property {Array<{action: string, detail: string, timestamp: number}>} steps - Execution trace
 * @property {Object} metadata - Arbitrary metadata (model, tokens, timing, etc.)
 */

/**
 * @typedef {'idle'|'running'|'paused'|'stopped'|'error'} AgentStatus
 */

/**
 * Base class for all Nova agents.
 * Provides Ollama chat, tool execution, state persistence, and streaming.
 *
 * @extends EventEmitter
 */
class BaseAgent extends EventEmitter {
  /**
   * @param {string} name - Human-readable agent name
   * @param {'on-demand'|'scheduled'|'continuous'} type - Agent lifecycle type
   * @param {Object} [config={}]
   * @param {string} [config.model] - Ollama model name
   * @param {number} [config.temperature] - Sampling temperature
   * @param {string} [config.systemPrompt] - System prompt prepended to every chat
   * @param {string} [config.ollamaUrl] - Ollama API base URL
   * @param {number} [config.requestTimeout] - HTTP timeout in ms
   * @param {Object} [config.tools] - Map of tool name → function
   */
  constructor(name, type, config = {}) {
    super();

    /** @type {string} */
    this.name = name;

    /** @type {'on-demand'|'scheduled'|'continuous'} */
    this.type = type;

    /** @type {string} */
    this.model = config.model || DEFAULT_MODEL;

    /** @type {number} */
    this.temperature = config.temperature ?? DEFAULT_TEMPERATURE;

    /** @type {string} */
    this.systemPrompt = config.systemPrompt || '';

    /** @type {string} */
    this.ollamaUrl = config.ollamaUrl || OLLAMA_URL;

    /** @type {number} */
    this.requestTimeout = config.requestTimeout || REQUEST_TIMEOUT;

    /** @type {Object<string, Function>} Registered tools */
    this.tools = config.tools || {};

    /** @type {AgentStatus} */
    this.status = 'idle';

    /** @type {Array<{action: string, detail: string, timestamp: number}>} */
    this.steps = [];

    /** @type {AbortController|null} */
    this._abort = null;

    /** @type {Array<{role: string, content: string}>} */
    this._messages = [];

    /** @type {number} */
    this._startTime = 0;
  }

  // ── Public API ──────────────────────────────────────────────────

  /**
   * Main entry point. Override in subclasses for custom logic.
   *
   * @param {string} input - User input / task description
   * @param {Object} [context={}] - Additional context (conversation history, etc.)
   * @returns {Promise<AgentResult>}
   */
  async run(input, context = {}) {
    this._prepare();
    try {
      const response = await this.think(input);
      this._addStep('think', response.slice(0, 200));
      return this._result(response);
    } catch (err) {
      return this._error(err);
    } finally {
      this._cleanup();
    }
  }

  /**
   * Send a prompt to Ollama and get a text response.
   *
   * @param {string} prompt - The user/assistant prompt
   * @param {Object} [options={}]
   * @param {boolean} [options.stream=false] - If true, emits 'chunk' events
   * @param {Array<{role:string, content:string}>} [options.messages] - Full message history override
   * @param {string} [options.systemPrompt] - Override system prompt for this call
   * @returns {Promise<string>} Full response text
   */
  async think(prompt, options = {}) {
    const messages = options.messages || this._buildMessages(prompt, options.systemPrompt);
    const stream = options.stream || false;

    const body = JSON.stringify({
      model: this.model,
      messages,
      stream,
      options: {
        temperature: this.temperature,
      },
    });

    return new Promise((resolve, reject) => {
      if (this.status === 'stopped') {
        return reject(new Error('Agent was stopped'));
      }

      const url = new URL(`${this.ollamaUrl}/api/chat`);
      const reqOptions = {
        hostname: url.hostname,
        port: url.port || 11434,
        path: url.pathname,
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(body),
        },
        timeout: this.requestTimeout,
      };

      const req = http.request(reqOptions, (res) => {
        let data = '';
        let fullResponse = '';

        res.on('data', (chunk) => {
          data += chunk.toString();

          if (stream) {
            // Ollama streams newline-delimited JSON
            const lines = data.split('\n');
            data = lines.pop() || ''; // keep incomplete line

            for (const line of lines) {
              if (!line.trim()) continue;
              try {
                const parsed = JSON.parse(line);
                if (parsed.message?.content) {
                  fullResponse += parsed.message.content;
                  this.emit('chunk', parsed.message.content);
                }
                if (parsed.done) {
                  this.emit('done', { response: fullResponse, ...parsed });
                }
              } catch (_) {
                // Incomplete JSON, skip
              }
            }
          }
        });

        res.on('end', () => {
          if (stream) {
            // Process any remaining data
            if (data.trim()) {
              try {
                const parsed = JSON.parse(data);
                if (parsed.message?.content) {
                  fullResponse += parsed.message.content;
                }
              } catch (_) {}
            }
            // Track assistant message
            this._messages.push({ role: 'assistant', content: fullResponse });
            resolve(fullResponse);
          } else {
            try {
              const parsed = JSON.parse(data);
              const content = parsed.message?.content || '';
              this._messages.push({ role: 'assistant', content });
              resolve(content);
            } catch (err) {
              reject(new Error(`Failed to parse Ollama response: ${err.message}`));
            }
          }
        });

        res.on('error', reject);
      });

      req.on('timeout', () => {
        req.destroy();
        reject(new Error(`Ollama request timed out after ${this.requestTimeout}ms`));
      });

      req.on('error', (err) => {
        if (err.code === 'ECONNREFUSED') {
          reject(new Error('Ollama is not running. Start it or check the URL.'));
        } else {
          reject(err);
        }
      });

      // Store ref so stop() can abort
      this._abort = { destroy: () => req.destroy() };

      req.write(body);
      req.end();
    });
  }

  /**
   * Execute a registered tool by name.
   *
   * @param {string} toolName - Name of the tool
   * @param {Object} args - Arguments to pass
   * @returns {Promise<{success: boolean, result?: any, error?: string}>}
   */
  async useTool(toolName, args = {}) {
    const tool = this.tools[toolName];
    if (!tool) {
      return { success: false, error: `Unknown tool: ${toolName}` };
    }

    this._addStep('tool', `${toolName}(${JSON.stringify(args).slice(0, 100)})`);

    try {
      const result = await tool(args);
      return { success: true, result };
    } catch (err) {
      return { success: false, error: err.message };
    }
  }

  /**
   * Stop a running agent. Aborts any in-flight HTTP request.
   */
  stop() {
    this.status = 'stopped';
    if (this._abort) {
      this._abort.destroy();
      this._abort = null;
    }
    this.emit('stopped');
  }

  /**
   * Get serializable agent state for persistence.
   *
   * @returns {Object}
   */
  getState() {
    return {
      name: this.name,
      type: this.type,
      status: this.status,
      model: this.model,
      temperature: this.temperature,
      systemPrompt: this.systemPrompt,
      steps: this.steps,
      messages: this._messages,
    };
  }

  /**
   * Restore agent state from a previously saved state object.
   *
   * @param {Object} state
   */
  setState(state) {
    if (state.status) this.status = state.status;
    if (state.model) this.model = state.model;
    if (state.temperature != null) this.temperature = state.temperature;
    if (state.systemPrompt) this.systemPrompt = state.systemPrompt;
    if (state.steps) this.steps = state.steps;
    if (state.messages) this._messages = state.messages;
  }

  // ── Protected helpers (used by subclasses) ────────────────────────

  /**
   * Build the message array for an Ollama chat call.
   *
   * @param {string} userMessage
   * @param {string} [systemOverride]
   * @returns {Array<{role: string, content: string}>}
   * @protected
   */
  _buildMessages(userMessage, systemOverride) {
    const sys = systemOverride || this.systemPrompt;
    const messages = [];

    if (sys) {
      messages.push({ role: 'system', content: sys });
    }

    // Include conversation history
    for (const msg of this._messages) {
      messages.push(msg);
    }

    messages.push({ role: 'user', content: userMessage });
    this._messages.push({ role: 'user', content: userMessage });

    return messages;
  }

  /**
   * Try to extract a JSON object from LLM text output.
   * Looks for ```json blocks or raw { } content.
   *
   * @param {string} text
   * @returns {Object|null}
   * @protected
   */
  _extractJSON(text) {
    // Try fenced code block first
    const fenced = text.match(/```(?:json)?\s*\n?([\s\S]*?)```/);
    if (fenced) {
      try {
        return JSON.parse(fenced[1].trim());
      } catch (_) {}
    }

    // Try to find raw JSON object
    const braceStart = text.indexOf('{');
    const braceEnd = text.lastIndexOf('}');
    if (braceStart !== -1 && braceEnd > braceStart) {
      try {
        return JSON.parse(text.slice(braceStart, braceEnd + 1));
      } catch (_) {}
    }

    return null;
  }

  /**
   * Extract a tool call from LLM output. Expects JSON with { tool, args }.
   *
   * @param {string} text
   * @returns {{ tool: string, args: Object }|null}
   * @protected
   */
  _extractToolCall(text) {
    const json = this._extractJSON(text);
    if (json && json.tool && typeof json.tool === 'string') {
      return { tool: json.tool, args: json.args || {} };
    }
    return null;
  }

  /**
   * Add a step to the execution trace.
   *
   * @param {string} action
   * @param {string} detail
   * @protected
   */
  _addStep(action, detail) {
    const step = { action, detail, timestamp: Date.now() };
    this.steps.push(step);
    this.emit('step', step);
  }

  /**
   * Build a successful AgentResult.
   *
   * @param {string} output
   * @param {Object} [extraMeta={}]
   * @returns {AgentResult}
   * @protected
   */
  _result(output, extraMeta = {}) {
    return {
      output,
      steps: this.steps,
      metadata: {
        agent: this.name,
        type: this.type,
        model: this.model,
        duration: Date.now() - this._startTime,
        ...extraMeta,
      },
    };
  }

  /**
   * Build an error AgentResult.
   *
   * @param {Error} err
   * @returns {AgentResult}
   * @protected
   */
  _error(err) {
    this.status = 'error';
    this._addStep('error', err.message);
    return {
      output: `Error: ${err.message}`,
      steps: this.steps,
      metadata: {
        agent: this.name,
        type: this.type,
        model: this.model,
        duration: Date.now() - this._startTime,
        error: true,
        errorMessage: err.message,
      },
    };
  }

  /**
   * Prepare agent for a new run.
   * @protected
   */
  _prepare() {
    this.status = 'running';
    this.steps = [];
    this._startTime = Date.now();
    this._abort = null;
    this.emit('started');
  }

  /**
   * Clean up after a run completes.
   * @protected
   */
  _cleanup() {
    if (this.status === 'running') {
      this.status = 'idle';
    }
    this._abort = null;
    this.emit('finished');
  }

  /**
   * Generate the tool descriptions for a system prompt.
   * Used by agents that support tool calling.
   *
   * @returns {string}
   * @protected
   */
  _toolDescriptions() {
    const names = Object.keys(this.tools);
    if (names.length === 0) return '';

    const toolList = names.map((name) => {
      const fn = this.tools[name];
      const desc = fn.description || name;
      const params = fn.parameters || 'any';
      return `- ${name}: ${desc} (params: ${JSON.stringify(params)})`;
    }).join('\n');

    return [
      '\n\nYou have access to the following tools:',
      toolList,
      '',
      'To use a tool, respond with ONLY a JSON object:',
      '```json',
      '{"tool": "tool_name", "args": {"param": "value"}}',
      '```',
      '',
      'To give a final answer (no more tools needed), respond with:',
      '```json',
      '{"tool": "final_answer", "args": {"answer": "your response"}}',
      '```',
    ].join('\n');
  }
}

module.exports = BaseAgent;
