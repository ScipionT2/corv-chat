/**
 * @module react-agent
 * @description ReAct Agent — classic Thought → Action → Observation loop.
 * Implements the ReAct pattern with explicit reasoning traces and
 * interleaved tool use.
 */

'use strict';

const BaseAgent = require('./base-agent');

const MAX_ITERATIONS = 10;

const REACT_SYSTEM = `You are Nova ReAct Agent. You solve tasks using a strict Thought-Action-Observation loop.

## Process
1. **Thought**: Reason about the current state and what to do next
2. **Action**: Choose a tool to use (or give final answer)
3. **Observation**: Receive the tool result (provided by the system)
4. Repeat until you have enough information

## Response Format
You MUST respond in this exact format every time:

Thought: [your reasoning about the current situation]
Action: [tool_name]
Action Input: [JSON arguments for the tool]

OR when you have the final answer:

Thought: [your final reasoning]
Action: final_answer
Action Input: {"answer": "your complete response"}

## Rules
- Always start with "Thought:"
- Always include "Action:" and "Action Input:"
- One action per turn
- Use observations to inform next steps
- If a tool fails, try a different approach
- Give final_answer when you have enough information
`;

/**
 * @typedef {Object} ReActTrace
 * @property {string} thought - Agent's reasoning
 * @property {string} action - Tool name
 * @property {Object} actionInput - Tool arguments
 * @property {string} [observation] - Tool result
 */

/**
 * ReAct (Reasoning + Acting) agent with explicit reasoning traces.
 *
 * @extends BaseAgent
 */
class ReActAgent extends BaseAgent {
  /**
   * @param {Object} [config={}]
   * @param {number} [config.maxIterations=10]
   * @param {Object} [config.tools]
   */
  constructor(config = {}) {
    super('react', 'on-demand', {
      systemPrompt: REACT_SYSTEM,
      temperature: 0.3,
      ...config,
    });

    /** @type {number} */
    this.maxIterations = config.maxIterations || MAX_ITERATIONS;

    /** @type {ReActTrace[]} */
    this.traces = [];
  }

  /**
   * Run the ReAct loop.
   *
   * @param {string} input - User task
   * @param {Object} [context={}]
   * @returns {Promise<import('./base-agent').AgentResult>}
   */
  async run(input, context = {}) {
    this._prepare();
    this.traces = [];

    try {
      const systemWithTools = this.systemPrompt + this._toolDescriptions();
      this._messages = [];

      let currentInput = `Task: ${input}`;
      let iteration = 0;
      let finalAnswer = null;

      while (iteration < this.maxIterations && this.status === 'running') {
        iteration++;
        this._addStep('react-loop', `Iteration ${iteration}/${this.maxIterations}`);

        const response = await this.think(currentInput, {
          systemPrompt: systemWithTools,
        });

        // Parse the ReAct formatted response
        const parsed = this._parseReActResponse(response);

        if (!parsed) {
          // Couldn't parse — try JSON fallback
          const json = this._extractJSON(response);
          if (json?.tool === 'final_answer') {
            finalAnswer = json.args?.answer || response;
            break;
          }
          // Treat as final answer
          finalAnswer = response;
          break;
        }

        const trace = {
          thought: parsed.thought,
          action: parsed.action,
          actionInput: parsed.actionInput,
        };

        this._addStep('thought', parsed.thought.slice(0, 300));
        this.emit('thought', parsed.thought);

        // Check for final answer
        if (parsed.action === 'final_answer') {
          finalAnswer = parsed.actionInput?.answer || parsed.thought;
          this._addStep('final_answer', finalAnswer.slice(0, 200));
          trace.observation = '[DONE]';
          this.traces.push(trace);
          break;
        }

        // Execute the tool
        const toolResult = await this.useTool(parsed.action, parsed.actionInput);

        const observation = toolResult.success
          ? JSON.stringify(toolResult.result).slice(0, 2000)
          : `Error: ${toolResult.error}`;

        trace.observation = observation;
        this.traces.push(trace);

        this._addStep('observation', observation.slice(0, 300));
        this.emit('observation', { action: parsed.action, observation });

        // Feed observation back
        currentInput = `Observation: ${observation}`;
      }

      if (!finalAnswer) {
        // Synthesize from traces
        finalAnswer = await this._synthesizeFromTraces(input);
      }

      return this._result(finalAnswer, {
        iterations: iteration,
        traces: this.traces.map((t) => ({
          thought: t.thought.slice(0, 200),
          action: t.action,
          hasObservation: !!t.observation,
        })),
      });
    } catch (err) {
      return this._error(err);
    } finally {
      this._cleanup();
    }
  }

  /**
   * Parse a ReAct-formatted response.
   *
   * @param {string} text
   * @returns {{ thought: string, action: string, actionInput: Object }|null}
   * @private
   */
  _parseReActResponse(text) {
    // Extract Thought
    const thoughtMatch = text.match(/Thought:\s*([\s\S]*?)(?=\nAction:)/i);
    if (!thoughtMatch) return null;

    const thought = thoughtMatch[1].trim();

    // Extract Action
    const actionMatch = text.match(/Action:\s*(\S+)/i);
    if (!actionMatch) return null;

    const action = actionMatch[1].trim();

    // Extract Action Input
    let actionInput = {};
    const inputMatch = text.match(/Action Input:\s*([\s\S]*?)$/i);
    if (inputMatch) {
      const raw = inputMatch[1].trim();
      try {
        actionInput = JSON.parse(raw);
      } catch (_) {
        // Try extracting JSON from the string
        const json = this._extractJSON(raw);
        if (json) {
          actionInput = json;
        } else {
          // Treat as string input
          actionInput = { input: raw };
        }
      }
    }

    return { thought, action, actionInput };
  }

  /**
   * Synthesize a final answer from collected traces when max iterations hit.
   *
   * @param {string} originalTask
   * @returns {Promise<string>}
   * @private
   */
  async _synthesizeFromTraces(originalTask) {
    const traceText = this.traces
      .map((t, i) => [
        `Step ${i + 1}:`,
        `  Thought: ${t.thought}`,
        `  Action: ${t.action}`,
        `  Observation: ${t.observation || '(none)'}`,
      ].join('\n'))
      .join('\n\n');

    const prompt = `I was working on this task: "${originalTask}"

Here are the steps I took:
${traceText}

Based on these observations, provide the best possible answer.`;

    return this.think(prompt);
  }
}

/** @type {string} */
ReActAgent.agentType = 'react';

/** @type {string} */
ReActAgent.description = 'ReAct agent — Thought/Action/Observation loop with explicit reasoning traces';

/** @type {string} */
ReActAgent.category = 'on-demand';

module.exports = ReActAgent;
