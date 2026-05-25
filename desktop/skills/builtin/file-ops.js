'use strict';

const fs = require('fs');
const path = require('path');
const os = require('os');
const { BaseSkill } = require('../base-skill');

const HOME = os.homedir();

/**
 * Validate that a path is safely under the user's home directory.
 * Prevents path traversal attacks.
 * @param {string} filePath
 * @returns {string} Resolved absolute path.
 * @throws {Error} If path escapes home directory.
 */
function safePath(filePath) {
  if (!filePath || typeof filePath !== 'string') {
    throw new Error('Path must be a non-empty string');
  }
  const resolved = path.resolve(filePath);
  if (!resolved.startsWith(HOME + path.sep) && resolved !== HOME) {
    throw new Error(`Access denied: path must be under ${HOME}`);
  }
  return resolved;
}

// ─── ReadFileSkill ───────────────────────────────────────────────────────────

/**
 * ReadFileSkill — reads a file and returns its contents.
 * Paths are restricted to the user's home directory.
 */
class ReadFileSkill extends BaseSkill {
  constructor() {
    super({
      name: 'read_file',
      description: 'Read the contents of a file. Path must be under the home directory.',
      version: '1.0.0',
      category: 'file',
      parameters: {
        type: 'object',
        properties: {
          path: {
            type: 'string',
            description: 'Absolute or relative path to the file',
          },
          encoding: {
            type: 'string',
            description: 'File encoding (default: utf-8)',
          },
        },
        required: ['path'],
      },
    });
  }

  /**
   * @param {{path: string, encoding?: string}} args
   * @returns {Promise<{content: string, size: number}>}
   */
  async execute(args) {
    const filePath = safePath(args.path);
    const encoding = args.encoding || 'utf-8';

    if (!fs.existsSync(filePath)) {
      throw new Error(`File not found: ${filePath}`);
    }

    const stat = fs.statSync(filePath);
    if (!stat.isFile()) {
      throw new Error(`Not a file: ${filePath}`);
    }

    // Limit read to 1MB to avoid memory issues
    if (stat.size > 1024 * 1024) {
      throw new Error(`File too large (${stat.size} bytes). Max 1MB.`);
    }

    const content = fs.readFileSync(filePath, encoding);
    return { content, size: stat.size };
  }
}

// ─── WriteFileSkill ──────────────────────────────────────────────────────────

/**
 * WriteFileSkill — writes content to a file, creating parent directories if needed.
 * Paths are restricted to the user's home directory.
 */
class WriteFileSkill extends BaseSkill {
  constructor() {
    super({
      name: 'write_file',
      description: 'Write content to a file. Creates parent directories if needed. Path must be under the home directory.',
      version: '1.0.0',
      category: 'file',
      parameters: {
        type: 'object',
        properties: {
          path: {
            type: 'string',
            description: 'Absolute or relative path to the file',
          },
          content: {
            type: 'string',
            description: 'Content to write to the file',
          },
          append: {
            type: 'boolean',
            description: 'Append instead of overwrite (default: false)',
          },
        },
        required: ['path', 'content'],
      },
    });
  }

  /**
   * @param {{path: string, content: string, append?: boolean}} args
   * @returns {Promise<{path: string, bytes: number}>}
   */
  async execute(args) {
    if (args.content === undefined || args.content === null) {
      throw new Error('Parameter "content" is required');
    }

    const filePath = safePath(args.path);
    const dir = path.dirname(filePath);

    // Create parent directories if they don't exist
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }

    if (args.append) {
      fs.appendFileSync(filePath, args.content, 'utf-8');
    } else {
      fs.writeFileSync(filePath, args.content, 'utf-8');
    }

    const stat = fs.statSync(filePath);
    return { path: filePath, bytes: stat.size };
  }
}

// ─── ListDirSkill ────────────────────────────────────────────────────────────

/**
 * ListDirSkill — lists directory contents with type annotations.
 * Paths are restricted to the user's home directory.
 */
class ListDirSkill extends BaseSkill {
  constructor() {
    super({
      name: 'list_dir',
      description: 'List contents of a directory. Returns entries with name, type, and size. Path must be under the home directory.',
      version: '1.0.0',
      category: 'file',
      parameters: {
        type: 'object',
        properties: {
          path: {
            type: 'string',
            description: 'Absolute or relative path to the directory',
          },
          showHidden: {
            type: 'boolean',
            description: 'Include hidden files (default: false)',
          },
        },
        required: ['path'],
      },
    });
  }

  /**
   * @param {{path: string, showHidden?: boolean}} args
   * @returns {Promise<{entries: {name: string, type: string, size: number}[], count: number}>}
   */
  async execute(args) {
    const dirPath = safePath(args.path);
    const showHidden = args.showHidden || false;

    if (!fs.existsSync(dirPath)) {
      throw new Error(`Directory not found: ${dirPath}`);
    }

    const stat = fs.statSync(dirPath);
    if (!stat.isDirectory()) {
      throw new Error(`Not a directory: ${dirPath}`);
    }

    let names = fs.readdirSync(dirPath);
    if (!showHidden) {
      names = names.filter((n) => !n.startsWith('.'));
    }

    // Cap listing at 500 entries
    const capped = names.slice(0, 500);

    const entries = capped.map((name) => {
      try {
        const entryPath = path.join(dirPath, name);
        const s = fs.statSync(entryPath);
        return {
          name,
          type: s.isDirectory() ? 'directory' : s.isSymbolicLink() ? 'symlink' : 'file',
          size: s.size,
        };
      } catch {
        return { name, type: 'unknown', size: 0 };
      }
    });

    return { entries, count: entries.length, total: names.length };
  }
}

// ─── FileInfoSkill ───────────────────────────────────────────────────────────

/**
 * FileInfoSkill — returns detailed metadata about a file or directory.
 * Paths are restricted to the user's home directory.
 */
class FileInfoSkill extends BaseSkill {
  constructor() {
    super({
      name: 'file_info',
      description: 'Get detailed metadata about a file or directory (size, timestamps, permissions). Path must be under the home directory.',
      version: '1.0.0',
      category: 'file',
      parameters: {
        type: 'object',
        properties: {
          path: {
            type: 'string',
            description: 'Absolute or relative path to the file or directory',
          },
        },
        required: ['path'],
      },
    });
  }

  /**
   * @param {{path: string}} args
   * @returns {Promise<{path: string, type: string, size: number, created: string, modified: string, accessed: string, permissions: string}>}
   */
  async execute(args) {
    const filePath = safePath(args.path);

    if (!fs.existsSync(filePath)) {
      throw new Error(`Path not found: ${filePath}`);
    }

    const stat = fs.statSync(filePath);
    const type = stat.isDirectory() ? 'directory' : stat.isSymbolicLink() ? 'symlink' : 'file';

    return {
      path: filePath,
      type,
      size: stat.size,
      created: stat.birthtime.toISOString(),
      modified: stat.mtime.toISOString(),
      accessed: stat.atime.toISOString(),
      permissions: '0' + (stat.mode & 0o777).toString(8),
      isReadonly: !(stat.mode & 0o200),
    };
  }
}

// Export all four skills as an array
module.exports = {
  skills: [
    new ReadFileSkill(),
    new WriteFileSkill(),
    new ListDirSkill(),
    new FileInfoSkill(),
  ],
};
