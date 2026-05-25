'use strict';

const path = require('path');
const fs = require('fs');
const { execSync } = require('child_process');
const os = require('os');
const { BaseSkill } = require('./base-skill');

/**
 * SkillRegistry — central registry for all Nova skills.
 * Manages registration, lookup, execution, and dynamic loading.
 */
class SkillRegistry {
  constructor() {
    /** @type {Map<string, BaseSkill>} */
    this._skills = new Map();
  }

  /**
   * Register a skill instance.
   * @param {BaseSkill} skill - Must extend BaseSkill.
   * @throws {TypeError} If skill is not a BaseSkill instance.
   * @throws {Error} If a skill with the same name is already registered.
   */
  register(skill) {
    if (!(skill instanceof BaseSkill)) {
      throw new TypeError('Skill must extend BaseSkill');
    }
    if (this._skills.has(skill.name)) {
      throw new Error(`Skill "${skill.name}" is already registered`);
    }
    this._skills.set(skill.name, skill);
  }

  /**
   * Unregister a skill by name.
   * @param {string} name
   * @returns {boolean} True if the skill was found and removed.
   */
  unregister(name) {
    return this._skills.delete(name);
  }

  /**
   * Get a skill by name.
   * @param {string} name
   * @returns {BaseSkill|undefined}
   */
  get(name) {
    return this._skills.get(name);
  }

  /**
   * List all registered skills.
   * @returns {BaseSkill[]}
   */
  list() {
    return Array.from(this._skills.values());
  }

  /**
   * List all skills in OpenAI function-calling tool spec format.
   * @returns {object[]}
   */
  listForLLM() {
    return this.list().map((s) => s.toToolSpec());
  }

  /**
   * Execute a skill by name with the given args.
   * @param {string} skillName
   * @param {object} args
   * @returns {Promise<*>}
   */
  async execute(skillName, args = {}) {
    const skill = this._skills.get(skillName);
    if (!skill) {
      throw new Error(`Skill "${skillName}" not found`);
    }
    return skill.execute(args);
  }

  /**
   * Auto-load all built-in skills from the builtin/ directory.
   * Each file may export one skill or an array of skills.
   */
  loadBuiltins() {
    const builtinDir = path.join(__dirname, 'builtin');
    if (!fs.existsSync(builtinDir)) {
      return;
    }

    const files = fs.readdirSync(builtinDir).filter((f) => f.endsWith('.js'));
    for (const file of files) {
      try {
        const mod = require(path.join(builtinDir, file));
        const skills = Array.isArray(mod) ? mod : (mod.skills || [mod]);

        for (const item of skills) {
          // Each export may be a skill instance or a class — instantiate classes
          if (item instanceof BaseSkill) {
            this.register(item);
          } else if (typeof item === 'function' && item.prototype instanceof BaseSkill) {
            this.register(new item());
          }
          // Skip anything else silently
        }
      } catch (err) {
        console.error(`[SkillRegistry] Failed to load builtin/${file}: ${err.message}`);
      }
    }
  }

  /**
   * Install a skill from a GitHub repo URL.
   * Clones into ~/.nova/skills/<repo-name> and loads exported skills.
   * @param {string} repoUrl - Full GitHub repo URL (https://github.com/user/repo)
   * @returns {string[]} Names of newly registered skills.
   */
  installFromGitHub(repoUrl) {
    if (!repoUrl || typeof repoUrl !== 'string') {
      throw new Error('repoUrl must be a non-empty string');
    }

    // Extract repo name from URL
    const match = repoUrl.match(/\/([^/]+?)(?:\.git)?$/);
    if (!match) {
      throw new Error(`Cannot parse repo name from URL: ${repoUrl}`);
    }
    const repoName = match[1];
    const skillsDir = path.join(os.homedir(), '.nova', 'skills');
    const targetDir = path.join(skillsDir, repoName);

    // Create skills directory if needed
    fs.mkdirSync(skillsDir, { recursive: true });

    if (fs.existsSync(targetDir)) {
      // Pull latest
      execSync('git pull', { cwd: targetDir, timeout: 30000 });
    } else {
      execSync(`git clone "${repoUrl}" "${targetDir}"`, { timeout: 60000 });
    }

    // Load the skill(s) from the cloned repo
    const added = [];
    try {
      const mod = require(targetDir);
      const skills = Array.isArray(mod) ? mod : (mod.skills || [mod]);

      for (const item of skills) {
        if (item instanceof BaseSkill) {
          this.register(item);
          added.push(item.name);
        } else if (typeof item === 'function' && item.prototype instanceof BaseSkill) {
          const inst = new item();
          this.register(inst);
          added.push(inst.name);
        }
      }
    } catch (err) {
      throw new Error(`Installed repo but failed to load skills: ${err.message}`);
    }

    return added;
  }

  /**
   * Get skills grouped by category.
   * @returns {Object<string, BaseSkill[]>}
   */
  getCategories() {
    const cats = {};
    for (const skill of this._skills.values()) {
      const cat = skill.category || 'uncategorized';
      if (!cats[cat]) {
        cats[cat] = [];
      }
      cats[cat].push(skill);
    }
    return cats;
  }
}

module.exports = { SkillRegistry, BaseSkill };
