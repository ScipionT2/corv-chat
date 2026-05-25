/**
 * @module research
 * @description Deep Research Agent — multi-hop research with citations.
 * Gathers information from multiple sources, synthesizes findings,
 * and returns a structured report with source attribution.
 */

'use strict';

const BaseAgent = require('./base-agent');

const MAX_HOPS = 6;

const RESEARCH_SYSTEM = `You are Nova Research Agent, a deep research AI that gathers information from multiple sources and synthesizes comprehensive reports.

## How You Work
1. Break the research topic into specific questions
2. Search for information one question at a time
3. Evaluate source quality and relevance
4. Synthesize findings into a structured report
5. Always cite your sources

## Rules
- Search for multiple perspectives on a topic
- Cross-reference information across sources
- Note when sources disagree
- Clearly distinguish facts from speculation
- Always provide citations

## Response Format
Always respond with a JSON object:

To search for information:
\`\`\`json
{"thought": "what I want to find out", "tool": "web-search", "args": {"query": "search query"}}
\`\`\`

To write the final report:
\`\`\`json
{"thought": "ready to synthesize", "tool": "final_answer", "args": {"answer": "structured report with citations"}}
\`\`\`
`;

/**
 * @typedef {Object} ResearchSource
 * @property {string} query - Search query used
 * @property {string} content - Source content / summary
 * @property {number} hop - Which research hop this came from
 */

/**
 * Deep Research agent with multi-hop information gathering and citation.
 *
 * @extends BaseAgent
 */
class ResearchAgent extends BaseAgent {
  /**
   * @param {Object} [config={}]
   * @param {number} [config.maxHops=6] - Maximum research hops
   * @param {Object} [config.tools] - Must include 'web-search' for full functionality
   */
  constructor(config = {}) {
    super('research', 'on-demand', {
      systemPrompt: RESEARCH_SYSTEM,
      temperature: 0.2, // Low temp for factual research
      ...config,
    });

    /** @type {number} */
    this.maxHops = config.maxHops || MAX_HOPS;

    /** @type {ResearchSource[]} */
    this.sources = [];
  }

  /**
   * Run a deep research task.
   *
   * @param {string} input - Research topic / question
   * @param {Object} [context={}]
   * @param {string} [context.depth='standard'] - 'quick' | 'standard' | 'deep'
   * @returns {Promise<import('./base-agent').AgentResult>}
   */
  async run(input, context = {}) {
    this._prepare();
    this.sources = [];

    const depth = context.depth || 'standard';
    const maxHops = depth === 'quick' ? 2 : depth === 'deep' ? this.maxHops * 2 : this.maxHops;

    try {
      const systemWithTools = this.systemPrompt + this._toolDescriptions();

      this._messages = [];
      let currentInput = `Research topic: ${input}\n\nDepth: ${depth}. Plan your research strategy, then start searching.`;
      let hop = 0;
      let finalReport = null;

      while (hop < maxHops && this.status === 'running') {
        hop++;
        this._addStep('hop', `Research hop ${hop}/${maxHops}`);

        const response = await this.think(currentInput, {
          systemPrompt: systemWithTools,
        });

        const parsed = this._extractJSON(response);

        if (!parsed) {
          // Unstructured response — may be the report itself
          finalReport = response;
          break;
        }

        if (parsed.thought) {
          this._addStep('thought', parsed.thought.slice(0, 300));
          this.emit('thought', parsed.thought);
        }

        // Final report
        if (parsed.tool === 'final_answer') {
          finalReport = parsed.args?.answer || response;
          break;
        }

        // Execute search or other tool
        if (parsed.tool) {
          const toolResult = await this.useTool(parsed.tool, parsed.args || {});

          const resultStr = toolResult.success
            ? JSON.stringify(toolResult.result).slice(0, 3000)
            : `Error: ${toolResult.error}`;

          // Track source
          this.sources.push({
            query: parsed.args?.query || parsed.tool,
            content: resultStr.slice(0, 1000),
            hop,
          });

          this._addStep('source', `[${hop}] ${parsed.args?.query || parsed.tool}`);
          this.emit('source', this.sources[this.sources.length - 1]);

          currentInput = `Search result for "${parsed.args?.query || ''}":\n${resultStr}\n\n` +
            `Sources gathered so far: ${this.sources.length}. ` +
            `Research hops remaining: ${maxHops - hop}. ` +
            `Continue researching or synthesize your final report.`;
        }
      }

      // If no explicit report, synthesize one
      if (!finalReport) {
        finalReport = await this._synthesize(input);
      }

      // Append sources section if not already present
      if (!finalReport.toLowerCase().includes('source') && this.sources.length > 0) {
        finalReport += this._formatSources();
      }

      return this._result(finalReport, {
        hops: hop,
        maxHops,
        depth,
        sourceCount: this.sources.length,
        sources: this.sources.map((s) => ({ query: s.query, hop: s.hop })),
      });
    } catch (err) {
      return this._error(err);
    } finally {
      this._cleanup();
    }
  }

  /**
   * Synthesize a final report from gathered sources.
   *
   * @param {string} topic
   * @returns {Promise<string>}
   * @private
   */
  async _synthesize(topic) {
    const sourceSummary = this.sources
      .map((s, i) => `[${i + 1}] Query: "${s.query}"\nContent: ${s.content}`)
      .join('\n\n');

    const prompt = `Based on the following research sources, write a comprehensive report on: "${topic}"

## Sources
${sourceSummary || '(No sources gathered)'}

## Instructions
- Write a well-structured report with sections
- Cite sources using [1], [2], etc.
- Note any conflicting information
- End with key takeaways`;

    this._addStep('synthesize', 'Generating final report from sources');
    return this.think(prompt);
  }

  /**
   * Format sources as a citation block.
   *
   * @returns {string}
   * @private
   */
  _formatSources() {
    if (this.sources.length === 0) return '';

    const citations = this.sources
      .map((s, i) => `[${i + 1}] "${s.query}" (hop ${s.hop})`)
      .join('\n');

    return `\n\n---\n## Sources\n${citations}`;
  }
}

/** @type {string} */
ResearchAgent.agentType = 'research';

/** @type {string} */
ResearchAgent.description = 'Deep research agent with multi-hop information gathering and citations';

/** @type {string} */
ResearchAgent.category = 'on-demand';

module.exports = ResearchAgent;
