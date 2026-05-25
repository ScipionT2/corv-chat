'use strict';

/**
 * Nova Memory Manager — High-level memory operations
 * 
 * Provides indexing, recall, and management for the memory system.
 * Coordinates store, embedder, and retriever.
 */

const fs = require('fs');
const path = require('path');
const store = require('./store');
const embedder = require('./embedder');
const retriever = require('./retriever');

// ── Configuration ───────────────────────────────────────────────────

const DEFAULT_CHUNK_SIZE = 500;    // characters per chunk
const DEFAULT_CHUNK_OVERLAP = 50;  // overlap between chunks
const SUPPORTED_EXTENSIONS = new Set([
  '.txt', '.md', '.js', '.ts', '.py', '.json', '.yaml', '.yml',
  '.html', '.css', '.sh', '.bash', '.zsh', '.env', '.toml', '.ini',
  '.cfg', '.conf', '.log', '.csv', '.xml', '.sql', '.rb', '.go',
  '.rs', '.java', '.c', '.cpp', '.h', '.hpp', '.swift', '.kt',
  '.r', '.R', '.lua', '.pl', '.jsx', '.tsx', '.vue', '.svelte',
]);

// ── Text Chunking ───────────────────────────────────────────────────

/**
 * Split text into overlapping chunks
 * @param {string} text
 * @param {number} chunkSize - Characters per chunk
 * @param {number} overlap - Overlap between chunks
 * @returns {string[]}
 */
function chunkText(text, chunkSize = DEFAULT_CHUNK_SIZE, overlap = DEFAULT_CHUNK_OVERLAP) {
  if (text.length <= chunkSize) return [text];

  const chunks = [];
  let start = 0;

  while (start < text.length) {
    let end = start + chunkSize;

    // Try to break at a natural boundary (newline, period, space)
    if (end < text.length) {
      const segment = text.slice(start, end);
      // Look for the last good break point in the last 20% of the chunk
      const breakZone = segment.slice(Math.floor(chunkSize * 0.8));
      const lastNewline = breakZone.lastIndexOf('\n');
      const lastPeriod = breakZone.lastIndexOf('. ');
      const lastSpace = breakZone.lastIndexOf(' ');

      const breakOffset = Math.floor(chunkSize * 0.8);
      if (lastNewline >= 0) {
        end = start + breakOffset + lastNewline + 1;
      } else if (lastPeriod >= 0) {
        end = start + breakOffset + lastPeriod + 2;
      } else if (lastSpace >= 0) {
        end = start + breakOffset + lastSpace + 1;
      }
    }

    const chunk = text.slice(start, end).trim();
    if (chunk.length > 0) {
      chunks.push(chunk);
    }

    start = end - overlap;
    if (start >= text.length) break;
  }

  return chunks;
}

// ── File Indexing ───────────────────────────────────────────────────

/**
 * Index a single file: read → chunk → embed → store
 * @param {string} filePath - Path to the file
 * @param {object} opts - { chunkSize?, tags?, force? }
 * @returns {Promise<{ indexed: number, source: string }>}
 */
async function index(filePath, opts = {}) {
  const absPath = path.resolve(filePath);

  if (!fs.existsSync(absPath)) {
    throw new Error(`File not found: ${absPath}`);
  }

  const stat = fs.statSync(absPath);
  if (!stat.isFile()) {
    throw new Error(`Not a file: ${absPath}`);
  }

  const ext = path.extname(absPath).toLowerCase();
  if (!SUPPORTED_EXTENSIONS.has(ext) && ext !== '') {
    throw new Error(`Unsupported file type: ${ext} (supported: text-based files)`);
  }

  // Read file
  const content = fs.readFileSync(absPath, 'utf-8');
  if (content.trim().length === 0) {
    return { indexed: 0, source: absPath, skipped: 'empty file' };
  }

  // Remove existing chunks for this file (re-index)
  if (opts.force !== false) {
    const existing = store.listAll().filter(
      d => d.metadata.source === absPath
    );
    for (const doc of existing) {
      store.remove(doc.id);
    }
  }

  // Chunk the content
  const chunks = chunkText(content, opts.chunkSize || DEFAULT_CHUNK_SIZE);

  // Embed all chunks
  const { embeddings, source: embedSource } = await embedder.embedBatch(
    chunks,
    opts
  );

  // Store each chunk
  const tags = opts.tags || [];
  const fileName = path.basename(absPath);

  for (let i = 0; i < chunks.length; i++) {
    store.add({
      content: chunks[i],
      metadata: {
        source: absPath,
        fileName,
        chunkIndex: i,
        totalChunks: chunks.length,
        tags: [...tags, ext.replace('.', ''), 'file'],
      },
      embedding: embeddings[i],
    });
  }

  store.flush();

  return {
    indexed: chunks.length,
    source: absPath,
    embedSource,
    fileSize: stat.size,
  };
}

/**
 * Recursively index a directory
 * @param {string} dirPath
 * @param {object} opts - { chunkSize?, tags?, maxFiles?, ignore? }
 * @returns {Promise<{ files: number, chunks: number, errors: string[] }>}
 */
async function indexDir(dirPath, opts = {}) {
  const absPath = path.resolve(dirPath);

  if (!fs.existsSync(absPath) || !fs.statSync(absPath).isDirectory()) {
    throw new Error(`Not a directory: ${absPath}`);
  }

  const maxFiles = opts.maxFiles || 500;
  const ignore = new Set(opts.ignore || [
    'node_modules', '.git', '.DS_Store', 'dist', 'build',
    '__pycache__', '.next', '.venv', 'venv', '.env',
  ]);

  const results = { files: 0, chunks: 0, errors: [], skipped: 0 };

  async function walk(dir) {
    if (results.files >= maxFiles) return;

    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch (err) {
      results.errors.push(`${dir}: ${err.message}`);
      return;
    }

    for (const entry of entries) {
      if (results.files >= maxFiles) break;
      if (ignore.has(entry.name)) continue;
      if (entry.name.startsWith('.')) continue;

      const fullPath = path.join(dir, entry.name);

      if (entry.isDirectory()) {
        await walk(fullPath);
      } else if (entry.isFile()) {
        const ext = path.extname(entry.name).toLowerCase();
        if (!SUPPORTED_EXTENSIONS.has(ext)) {
          results.skipped++;
          continue;
        }

        // Skip large files (>1MB)
        try {
          const stat = fs.statSync(fullPath);
          if (stat.size > 1024 * 1024) {
            results.skipped++;
            continue;
          }
        } catch (_) {
          continue;
        }

        try {
          const result = await index(fullPath, opts);
          results.files++;
          results.chunks += result.indexed;
        } catch (err) {
          results.errors.push(`${fullPath}: ${err.message}`);
        }
      }
    }
  }

  await walk(absPath);
  store.flush();

  return results;
}

// ── Memory Operations ───────────────────────────────────────────────

/**
 * Store a memory (arbitrary text) with embedding
 * @param {string} text - The text to remember
 * @param {object} metadata - Additional metadata { tags?, source?, ... }
 * @returns {Promise<object>} The stored document
 */
async function remember(text, metadata = {}) {
  const { embedding } = await embedder.embed(text);

  const doc = store.add({
    content: text,
    metadata: {
      source: metadata.source || 'manual',
      tags: [...(metadata.tags || []), 'memory'],
      ...metadata,
    },
    embedding,
  });

  store.flush();
  return doc;
}

/**
 * Semantic search for memories
 * @param {string} query - Natural language query
 * @param {number} topK - Number of results
 * @param {object} filters - Metadata filters
 * @returns {Promise<object[]>}
 */
async function recall(query, topK = 5, filters = null) {
  return retriever.search(query, { topK, filters });
}

/**
 * Delete a memory by ID
 * @param {string} id
 * @returns {boolean}
 */
function forget(id) {
  const result = store.remove(id);
  if (result) store.flush();
  return result;
}

/**
 * Get memory system statistics
 * @returns {object}
 */
function stats() {
  const storeStats = store.stats();
  return {
    ...storeStats,
    chunkSize: DEFAULT_CHUNK_SIZE,
    supportedExtensions: [...SUPPORTED_EXTENSIONS].sort(),
  };
}

// ── Exports ─────────────────────────────────────────────────────────

module.exports = {
  // File indexing
  index,
  indexDir,

  // Memory operations
  remember,
  recall,
  forget,

  // Stats & management
  stats,
  chunkText,

  // Re-export sub-modules
  store,
  embedder,
  retriever,
};
