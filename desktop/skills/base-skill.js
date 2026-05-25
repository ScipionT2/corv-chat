'use strict';
/**
 * Base class for all Nova skills/tools.
 * Every skill must extend this and implement execute().
 */
class BaseSkill {
  constructor(manifest = {}) {
    this._name = manifest.name || 'unnamed';
    this._description = manifest.description || '';
    this._version = manifest.version || '1.0.0';
    this._category = manifest.category || 'utility';
    this._parameters = manifest.parameters || { type: 'object', properties: {} };
  }
  get name() { return this._name; }
  get description() { return this._description; }
  get version() { return this._version; }
  get category() { return this._category; }
  get parameters() { return this._parameters; }

  /** Execute the skill. Override in subclass. */
  async execute(args) { throw new Error('execute() not implemented'); }

  /** Returns OpenAI function-calling tool spec */
  toToolSpec() {
    return {
      type: 'function',
      function: {
        name: this._name,
        description: this._description,
        parameters: this._parameters,
      },
    };
  }
}
module.exports = { BaseSkill };
