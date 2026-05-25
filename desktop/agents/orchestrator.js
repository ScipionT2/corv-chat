/**
 * @module orchestrator
 * @description Orchestrator Agent — multi-turn reasoning with automatic tool selection.
 * Plans steps, executes tools, synthesizes results. The "brain" agent.
 */

'use strict';

const BaseAgent = require('./base-agent');

const MAX_ITERATIONS = 10;

const ORCHESTRATOR_SYSTEM = `You are Nova Orchestrator, an advanced AI agent that solves complex tasks by breaking them into steps and using tools.

## How You Work
1. Analyze the user's request
2. Plan what steps and tools are needed
3. Execute tools one at a time, observe results
4. Synthesize a final answer from all observations

## Rules
- Think step by step before acting
- Use one tool at a time
- After each tool result, decide if you need more tools or can answer
- If you have enough information, give the final answer
- Never loop more than 10 times
- If stuck, give the best answer you have

## Response Format
Always respond with a JSON object:

To use a tool:
\`\`\`json
{"thought": "reasoning about what to do", "tool": "tool_name", "args": {"param": "value"}}
\`\`\`

To give the final answer:
\`\`\`json
{"thought": "final reasoning", "tool": "final_answer", "args": {"answer": "your complete response"}}
\`\`\`
`;

/**
 * Orchestrator agent with multi-turn reasoning and automatic tool selection.
 *
 * @extends BaseAgent
 */
class OrchestratorAgent extends BaseAgent {
  /**
   * @param {Object} [config={}]
   * @param {number} [config.maxIterations=10] - Max reasoning loops
   * @param {Object} [config.tools] - Available tools
   */
  constructor(config = {}) {
    super('orchestrator', 'on-demand', {
      systemPrompt: ORCHESTRATOR_SYSTEM,
      temperature: 0.3, // Lower temp for more focused reasoning
      ...config,
    });

    /** @type {number} */
    this.maxIterations = config.maxIterations || MAX_ITERATIONS;
  }

  /**
   * Run the orchestrator loop.
   *
   * @param {string} input - User task / question
   * @param {Object} [context={}]
   * @returns {Promise<import('./base-agent').AgentResult>}
   */
  async run(input, context = {}) {
    this._prepare();

    try {
      // Build system prompt with available tools
      const systemWithTools = this.systemPrompt + this._toolDescriptions();

      // Reset conversation for this run
      this._messages = [];

      let currentInput = input;
      let finalAnswer = null;
      let iteration = 0;

      while (iteration < this.maxIterations && this.status === 'running') {
        iteration++;
        this._addStep('iteration', `Loop ${iteration}/${this.maxIterations}`);

        // Ask the LLM what to do
        const response = await this.think(currentInput, {
          systemPrompt: systemWithTools,
        });

        // Try to parse the structured response
        const parsed = this._extractJSON(response);

        if (!parsed) {
          // LLM gave a plain text response — treat as final answer
          this._addStep('plain-response', 'LLM responded without JSON structure');
          finalAnswer = response;
          break;
        }

        // Log the thought
        if (parsed.thought) {
          this._addStep('thought', parsed.thought.slice(0, 300));
          this.emit('thought', parsed.thought);
        }

        // Check for final answer
        if (parsed.tool === 'final_answer') {
          finalAnswer = parsed.args?.answer || parsed.args?.response || response;
          this._addStep('final_answer', finalAnswer.slice(0, 200));
          break;
        }

        // Execute the tool
        if (parsed.tool) {
          const toolResult = await this.useTool(parsed.tool, parsed.args || {});

          const observation = toolResult.success
            ? `Tool "${parsed.tool}" returned: ${JSON.stringify(toolResult.result).slice(0, 2000)}`
            : `Tool "${parsed.tool}" failed: ${toolResult.error}`;

          this._addStep('observation', observation.slice(0, 300));
          this.emit('observation', observation);

          // Feed observation back as next input
          currentInput = `Observation from tool "${parsed.tool}":\n${observation}\n\nBased on this result, what should we do next? If you have enough information, provide the final answer.`;
        } else {
          // No tool specified — treat as final answer
          finalAnswer = parsed.thought || response;
          break;
        }
      }

      // Forced stop if max iterations reached
      if (!finalAnswer) {
        finalAnswer = 'I reached the maximum number of reasoning steps. Here is what I found so far based on my observations.';
        this._addStep('max_iterations', 'Reached iteration limit');
      }

      return this._result(finalAnswer, {
        iterations: iteration,
        maxIterations: this.maxIterations,
        toolsUsed: this.steps.filter((s) => s.action === 'tool').map((s) => s.detail),
      });
    } catch (err) {
      return this._error(err);
    } finally {
      this._cleanup();
    }
  }
}

/** @type {string} */
OrchestratorAgent.agentType = 'orchestrator';

/** @type {string} */
OrchestratorAgent.description = 'Multi-turn reasoning agent with automatic tool selection and planning';

/** @type {string} */
OrchestratorAgent.category = 'on-demand';

module.exports = OrchestratorAgent;
