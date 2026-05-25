/**
 * @module simple
 * @description Simple Chat Agent — single-turn, no tools, just LLM conversation.
 * The lightest agent: sends user input to Ollama, returns the response.
 */

'use strict';

const BaseAgent = require('./base-agent');

/**
 * Simple single-turn chat agent.
 * No tool use, no multi-turn reasoning — just a clean LLM call.
 *
 * @extends BaseAgent
 */
class SimpleAgent extends BaseAgent {
  /**
   * @param {Object} [config={}]
   * @param {string} [config.model]
   * @param {number} [config.temperature]
   * @param {string} [config.systemPrompt]
   * @param {boolean} [config.stream=false] - Enable streaming
   */
  constructor(config = {}) {
    super('simple', 'on-demand', {
      systemPrompt: config.systemPrompt || 'You are Nova, a helpful AI assistant running locally. Be concise and direct.',
      ...config,
    });

    /** @type {boolean} */
    this.stream = config.stream || false;
  }

  /**
   * Run a single-turn chat.
   *
   * @param {string} input - User message
   * @param {Object} [context={}]
   * @param {Array<{role:string, content:string}>} [context.history] - Prior conversation messages
   * @param {boolean} [context.stream] - Override streaming for this call
   * @returns {Promise<import('./base-agent').AgentResult>}
   */
  async run(input, context = {}) {
    this._prepare();

    try {
      if (!input || !input.trim()) {
        return this._result('', { skipped: true, reason: 'empty input' });
      }

      // Merge prior conversation history if provided
      if (context.history && Array.isArray(context.history)) {
        this._messages = [...context.history];
      }

      const shouldStream = context.stream ?? this.stream;

      this._addStep('chat', `Sending to ${this.model}`);

      const response = await this.think(input, { stream: shouldStream });

      this._addStep('response', response.slice(0, 200));

      return this._result(response, {
        messageCount: this._messages.length,
        streamed: shouldStream,
      });
    } catch (err) {
      return this._error(err);
    } finally {
      this._cleanup();
    }
  }
}

/** @type {string} Agent type identifier */
SimpleAgent.agentType = 'simple';

/** @type {string} */
SimpleAgent.description = 'Single-turn chat agent — no tools, just LLM conversation';

/** @type {string} */
SimpleAgent.category = 'on-demand';

module.exports = SimpleAgent;
