/**
 * @module agents
 * @description Nova Agent Registry — registers, creates, and manages all agent types.
 * Auto-registers all built-in agents on load.
 */

'use strict';

// ── Built-in agents ─────────────────────────────────────────────────
const SimpleAgent = require('./simple');
const OrchestratorAgent = require('./orchestrator');
const ResearchAgent = require('./research');
const ReActAgent = require('./react-agent');
const CodeAgent = require('./code-agent');
const MonitorAgent = require('./monitor');
const OperativeAgent = require('./operative');
const DigestAgent = require('./digest');

/**
 * @typedef {Object} AgentMeta
 * @property {string} name - Registry name
 * @property {string} type - Agent type identifier
 * @property {string} description - Human description
 * @property {string} category - 'on-demand' | 'scheduled' | 'continuous'
 * @property {Function} AgentClass - Constructor
 */

/**
 * Agent Registry.
 * Central hub for registering, creating, and querying agent types.
 */
const registry = {
  /** @type {Map<string, { AgentClass: Function, meta: AgentMeta }>} */
  _agents: new Map(),

  /**
   * Register an agent class under a name.
   *
   * @param {string} name - Unique registry name
   * @param {Function} AgentClass - Class extending BaseAgent
   * @throws {Error} If name is already registered
   */
  register(name, AgentClass) {
    if (this._agents.has(name)) {
      throw new Error(`Agent "${name}" is already registered`);
    }

    if (!AgentClass || typeof AgentClass !== 'function') {
      throw new Error(`Invalid agent class for "${name}"`);
    }

    const meta = {
      name,
      type: AgentClass.agentType || name,
      description: AgentClass.description || `${name} agent`,
      category: AgentClass.category || 'on-demand',
      AgentClass,
    };

    this._agents.set(name, { AgentClass, meta });
  },

  /**
   * Create an instance of a registered agent.
   *
   * @param {string} name - Registry name
   * @param {Object} [config={}] - Configuration passed to the constructor
   * @returns {import('./base-agent')} Agent instance
   * @throws {Error} If agent is not registered
   */
  create(name, config = {}) {
    const entry = this._agents.get(name);
    if (!entry) {
      const available = [...this._agents.keys()].join(', ');
      throw new Error(`Unknown agent "${name}". Available: ${available}`);
    }

    return new entry.AgentClass(config);
  },

  /**
   * List all registered agents with their metadata.
   *
   * @returns {AgentMeta[]}
   */
  list() {
    return [...this._agents.values()].map((entry) => ({ ...entry.meta }));
  },

  /**
   * Get available agent type categories.
   *
   * @returns {{ type: string, agents: string[] }[]}
   */
  getTypes() {
    const categories = {};

    for (const [name, entry] of this._agents) {
      const cat = entry.meta.category;
      if (!categories[cat]) {
        categories[cat] = [];
      }
      categories[cat].push(name);
    }

    return Object.entries(categories).map(([type, agents]) => ({ type, agents }));
  },

  /**
   * Check if an agent name is registered.
   *
   * @param {string} name
   * @returns {boolean}
   */
  has(name) {
    return this._agents.has(name);
  },

  /**
   * Get metadata for a specific agent.
   *
   * @param {string} name
   * @returns {AgentMeta|null}
   */
  get(name) {
    const entry = this._agents.get(name);
    return entry ? { ...entry.meta } : null;
  },

  /**
   * Unregister an agent.
   *
   * @param {string} name
   * @returns {boolean}
   */
  unregister(name) {
    return this._agents.delete(name);
  },

  /**
   * Get count of registered agents.
   *
   * @returns {number}
   */
  get size() {
    return this._agents.size;
  },
};

// ── Auto-register built-in agents ───────────────────────────────────
registry.register('simple', SimpleAgent);
registry.register('orchestrator', OrchestratorAgent);
registry.register('research', ResearchAgent);
registry.register('react', ReActAgent);
registry.register('code', CodeAgent);
registry.register('monitor', MonitorAgent);
registry.register('operative', OperativeAgent);
registry.register('digest', DigestAgent);

// ── Exports ─────────────────────────────────────────────────────────
module.exports = {
  registry,
  // Also export classes directly for advanced usage
  BaseAgent: require('./base-agent'),
  SimpleAgent,
  OrchestratorAgent,
  ResearchAgent,
  ReActAgent,
  CodeAgent,
  MonitorAgent,
  OperativeAgent,
  DigestAgent,
};
