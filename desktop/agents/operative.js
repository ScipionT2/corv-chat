/**
 * @module operative
 * @description Persistent Operative Agent — long-running autonomous agent.
 * Manages state (pause/resume), task queue, and memory across sessions.
 */

'use strict';

const BaseAgent = require('./base-agent');
const fs = require('fs');
const path = require('path');

const MAX_TASKS_PER_CYCLE = 5;

const OPERATIVE_SYSTEM = `You are Nova Operative, a persistent autonomous agent that manages and executes a queue of tasks over time.

## Your Role
- Process tasks from your queue one at a time
- Use tools to complete tasks
- Track progress and update task status
- Remember context across multiple runs
- Prioritize tasks intelligently

## Task Processing
For each task, respond with:
\`\`\`json
{
  "thought": "your reasoning about the task",
  "action": "tool_name" or "complete" or "defer",
  "args": {},
  "taskUpdate": {
    "status": "in_progress" | "completed" | "failed" | "deferred",
    "progress": "description of progress",
    "result": "result if completed"
  }
}
\`\`\`

"defer" means you need more information or want to come back to this task later.
`;

/**
 * @typedef {Object} Task
 * @property {string} id - Unique task ID
 * @property {string} description - Task description
 * @property {'queued'|'in_progress'|'completed'|'failed'|'deferred'|'paused'} status
 * @property {number} priority - Higher = more important (default 5)
 * @property {number} createdAt
 * @property {number} [updatedAt]
 * @property {string} [result]
 * @property {string[]} [log] - Progress log entries
 * @property {Object} [context] - Task-specific context
 */

/**
 * Persistent operative agent with task queue, state management, and cross-session memory.
 *
 * @extends BaseAgent
 */
class OperativeAgent extends BaseAgent {
  /**
   * @param {Object} [config={}]
   * @param {string} [config.storePath] - Path to persist state
   * @param {number} [config.maxTasksPerCycle=5]
   * @param {Object} [config.tools]
   */
  constructor(config = {}) {
    super('operative', 'continuous', {
      systemPrompt: OPERATIVE_SYSTEM,
      temperature: 0.3,
      ...config,
    });

    /** @type {string} */
    this.storePath = config.storePath || '';

    /** @type {number} */
    this.maxTasksPerCycle = config.maxTasksPerCycle || MAX_TASKS_PER_CYCLE;

    /** @type {Task[]} */
    this.tasks = [];

    /** @type {Object<string, any>} Cross-session memory */
    this.memory = {};

    /** @type {number} */
    this._nextId = 1;

    /** @type {boolean} */
    this._paused = false;

    // Load persisted state
    this._loadState();
  }

  // ── Task Management API ───────────────────────────────────────────

  /**
   * Add a task to the queue.
   *
   * @param {string} description - Task description
   * @param {Object} [options={}]
   * @param {number} [options.priority=5] - Task priority (1-10)
   * @param {Object} [options.context] - Additional context
   * @returns {Task} The created task
   */
  addTask(description, options = {}) {
    const task = {
      id: `task-${this._nextId++}`,
      description,
      status: 'queued',
      priority: options.priority ?? 5,
      createdAt: Date.now(),
      log: [],
      context: options.context || {},
    };

    this.tasks.push(task);
    this._sortTasks();
    this._persistState();
    this.emit('task-added', task);

    return task;
  }

  /**
   * Get a task by ID.
   *
   * @param {string} id
   * @returns {Task|undefined}
   */
  getTask(id) {
    return this.tasks.find((t) => t.id === id);
  }

  /**
   * Get all tasks, optionally filtered by status.
   *
   * @param {string} [status]
   * @returns {Task[]}
   */
  getTasks(status) {
    if (status) {
      return this.tasks.filter((t) => t.status === status);
    }
    return [...this.tasks];
  }

  /**
   * Remove a task from the queue.
   *
   * @param {string} id
   * @returns {boolean}
   */
  removeTask(id) {
    const idx = this.tasks.findIndex((t) => t.id === id);
    if (idx !== -1) {
      this.tasks.splice(idx, 1);
      this._persistState();
      return true;
    }
    return false;
  }

  /**
   * Pause the operative. Current task completes, then stops processing.
   */
  pause() {
    this._paused = true;
    this.status = 'paused';
    this._persistState();
    this.emit('paused');
  }

  /**
   * Resume the operative after a pause.
   */
  resume() {
    this._paused = false;
    this.status = 'idle';
    this._persistState();
    this.emit('resumed');
  }

  /**
   * Store something in cross-session memory.
   *
   * @param {string} key
   * @param {any} value
   */
  remember(key, value) {
    this.memory[key] = value;
    this._persistState();
  }

  /**
   * Recall something from memory.
   *
   * @param {string} key
   * @returns {any}
   */
  recall(key) {
    return this.memory[key];
  }

  // ── Main Run Loop ─────────────────────────────────────────────────

  /**
   * Process the next batch of queued tasks.
   *
   * @param {string} [input] - Optional directive (e.g., "focus on task-3")
   * @param {Object} [context={}]
   * @returns {Promise<import('./base-agent').AgentResult>}
   */
  async run(input, context = {}) {
    this._prepare();

    if (this._paused) {
      return this._result('Operative is paused. Use resume() to continue.', { paused: true });
    }

    try {
      // If input specifies a task, prioritize it
      if (input) {
        const specificTask = this.tasks.find((t) => t.id === input || t.description.includes(input));
        if (specificTask) {
          specificTask.priority = 10;
          this._sortTasks();
        } else {
          // Treat input as a new task
          this.addTask(input, { priority: 8 });
        }
      }

      const queued = this.getTasks('queued');
      if (queued.length === 0) {
        return this._result('No tasks in queue.', { taskCount: this.tasks.length });
      }

      const batch = queued.slice(0, this.maxTasksPerCycle);
      const results = [];

      for (const task of batch) {
        if (this.status !== 'running' || this._paused) break;

        this._addStep('process-task', `Processing: ${task.id} — ${task.description.slice(0, 100)}`);
        this.emit('task-started', task);

        task.status = 'in_progress';
        task.updatedAt = Date.now();

        const taskResult = await this._processTask(task);
        results.push(taskResult);

        this._persistState();
      }

      const summary = results.map((r) =>
        `${r.id}: ${r.status} — ${(r.result || r.progress || '').slice(0, 100)}`
      ).join('\n');

      return this._result(summary, {
        processed: results.length,
        remaining: this.getTasks('queued').length,
        completed: this.getTasks('completed').length,
        failed: this.getTasks('failed').length,
        results,
      });
    } catch (err) {
      return this._error(err);
    } finally {
      this._cleanup();
    }
  }

  /**
   * Process a single task through the LLM.
   *
   * @param {Task} task
   * @returns {Promise<{id: string, status: string, result?: string, progress?: string}>}
   * @private
   */
  async _processTask(task) {
    const systemWithTools = this.systemPrompt + this._toolDescriptions();

    // Build task context
    const memoryContext = Object.keys(this.memory).length > 0
      ? `\n\nMemory:\n${JSON.stringify(this.memory, null, 2).slice(0, 1000)}`
      : '';

    const taskContext = task.context && Object.keys(task.context).length > 0
      ? `\n\nTask context:\n${JSON.stringify(task.context, null, 2).slice(0, 500)}`
      : '';

    const logContext = task.log && task.log.length > 0
      ? `\n\nPrevious progress:\n${task.log.slice(-5).join('\n')}`
      : '';

    const prompt = `## Task: ${task.id}
Priority: ${task.priority}/10
Description: ${task.description}
${taskContext}${logContext}${memoryContext}

Process this task. Use tools if needed. Report your progress.`;

    // Reset conversation for this task
    this._messages = [];

    let maxAttempts = 3;
    let attempt = 0;

    while (attempt < maxAttempts && this.status === 'running') {
      attempt++;

      const response = await this.think(prompt, { systemPrompt: systemWithTools });
      const parsed = this._extractJSON(response);

      if (!parsed) {
        task.status = 'completed';
        task.result = response;
        task.updatedAt = Date.now();
        task.log = task.log || [];
        task.log.push(`[${new Date().toISOString()}] Completed: ${response.slice(0, 200)}`);
        this.emit('task-completed', task);
        return { id: task.id, status: 'completed', result: response };
      }

      // Update task log
      if (parsed.thought) {
        task.log = task.log || [];
        task.log.push(`[${new Date().toISOString()}] ${parsed.thought.slice(0, 200)}`);
      }

      // Handle task update
      if (parsed.taskUpdate) {
        const update = parsed.taskUpdate;

        if (update.status === 'completed' || parsed.action === 'complete') {
          task.status = 'completed';
          task.result = update.result || parsed.thought || '';
          task.updatedAt = Date.now();
          this.emit('task-completed', task);
          return { id: task.id, status: 'completed', result: task.result };
        }

        if (update.status === 'deferred' || parsed.action === 'defer') {
          task.status = 'deferred';
          task.updatedAt = Date.now();
          task.log.push(`[${new Date().toISOString()}] Deferred: ${update.progress || ''}`);
          this.emit('task-deferred', task);
          return { id: task.id, status: 'deferred', progress: update.progress };
        }

        if (update.status === 'failed') {
          task.status = 'failed';
          task.result = update.result || 'Task failed';
          task.updatedAt = Date.now();
          this.emit('task-failed', task);
          return { id: task.id, status: 'failed', result: task.result };
        }

        if (update.progress) {
          task.log.push(`[${new Date().toISOString()}] Progress: ${update.progress}`);
        }
      }

      // Execute tool if specified
      if (parsed.action && parsed.action !== 'complete' && parsed.action !== 'defer') {
        const toolResult = await this.useTool(parsed.action, parsed.args || {});
        task.log.push(`[${new Date().toISOString()}] Tool ${parsed.action}: ${toolResult.success ? 'OK' : toolResult.error}`);

        // Store relevant results in memory
        if (toolResult.success && parsed.thought?.includes('remember')) {
          this.remember(`task-${task.id}-result`, toolResult.result);
        }
      }
    }

    // Default: mark as completed if we exhausted attempts
    task.status = 'completed';
    task.updatedAt = Date.now();
    return { id: task.id, status: 'completed', progress: 'Exhausted processing attempts' };
  }

  // ── State Persistence ─────────────────────────────────────────────

  /**
   * Get full serializable state.
   *
   * @returns {Object}
   */
  getState() {
    return {
      ...super.getState(),
      tasks: this.tasks,
      memory: this.memory,
      nextId: this._nextId,
      paused: this._paused,
    };
  }

  /**
   * Restore full state.
   *
   * @param {Object} state
   */
  setState(state) {
    super.setState(state);
    if (state.tasks) this.tasks = state.tasks;
    if (state.memory) this.memory = state.memory;
    if (state.nextId) this._nextId = state.nextId;
    if (state.paused != null) this._paused = state.paused;
  }

  /**
   * Sort tasks by priority (descending), then creation time (ascending).
   * @private
   */
  _sortTasks() {
    this.tasks.sort((a, b) => {
      if (a.priority !== b.priority) return b.priority - a.priority;
      return a.createdAt - b.createdAt;
    });
  }

  /**
   * Persist state to disk.
   * @private
   */
  _persistState() {
    if (!this.storePath) return;
    try {
      const dir = path.dirname(this.storePath);
      if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
      }
      fs.writeFileSync(this.storePath, JSON.stringify({
        tasks: this.tasks,
        memory: this.memory,
        nextId: this._nextId,
        paused: this._paused,
        lastSaved: Date.now(),
      }, null, 2), 'utf-8');
    } catch (_) {
      // Best effort
    }
  }

  /**
   * Load state from disk.
   * @private
   */
  _loadState() {
    if (!this.storePath) return;
    try {
      if (fs.existsSync(this.storePath)) {
        const data = JSON.parse(fs.readFileSync(this.storePath, 'utf-8'));
        this.tasks = data.tasks || [];
        this.memory = data.memory || {};
        this._nextId = data.nextId || 1;
        this._paused = data.paused || false;
      }
    } catch (_) {
      // Fresh start
    }
  }
}

/** @type {string} */
OperativeAgent.agentType = 'operative';

/** @type {string} */
OperativeAgent.description = 'Persistent autonomous agent with task queue, state management, and cross-session memory';

/** @type {string} */
OperativeAgent.category = 'continuous';

module.exports = OperativeAgent;
