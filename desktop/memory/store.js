'use strict';

/**
 * Nova Memory Store — JSON file-backed document store
 * 
 * Stores documents with content, metadata, and embedding vectors.
 * Persists to ~/.nova/memory.json with atomic writes.
 */

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const NOVA_DIR = path.join(require('os').homedir(), '.nova');
const STORE_PATH = path.join(NOVA_DIR, 'memory.json');

// In-memory cache
let _store = null;
let _dirty = false;
let _flushTimer = null;

// ── Helpers ─────────────────────────────────────────────────────────

function ensureDir() {
  if (!fs.existsSync(NOVA_DIR)) {
    fs.mkdirSync(NOVA_DIR, { recursive: true });
  }
}

function generateId() {
  return crypto.randomBytes(12).toString('hex');
}

function now() {
  return new Date().toISOString();
}

// ── Load / Save ─────────────────────────────────────────────────────

function load() {
  if (_store !== null) return _store;
  ensureDir();
  if (fs.existsSync(STORE_PATH)) {
    try {
      const raw = fs.readFileSync(STORE_PATH, 'utf-8');
      _store = JSON.parse(raw);
      // Migrate from older formats
      if (!_store.version) {
        _store = { version: 1, documents: _store.documents || {} };
      }
    } catch (err) {
      console.error('[memory/store] Corrupt store, starting fresh:', err.message);
      _store = { version: 1, documents: {} };
    }
  } else {
    _store = { version: 1, documents: {} };
  }
  return _store;
}

function save() {
  ensureDir();
  const store = load();
  const tmp = STORE_PATH + '.tmp';
  try {
    fs.writeFileSync(tmp, JSON.stringify(store, null, 2), 'utf-8');
    fs.renameSync(tmp, STORE_PATH);
    _dirty = false;
  } catch (err) {
    console.error('[memory/store] Save failed:', err.message);
    // Clean up temp file
    try { fs.unlinkSync(tmp); } catch (_) {}
    throw err;
  }
}

function markDirty() {
  _dirty = true;
  // Debounced auto-save: flush within 2 seconds
  if (!_flushTimer) {
    _flushTimer = setTimeout(() => {
      _flushTimer = null;
      if (_dirty) save();
    }, 2000);
  }
}

// ── CRUD Operations ─────────────────────────────────────────────────

/**
 * Add a document to the store
 * @param {object} doc - { content, metadata?, embedding? }
 * @returns {object} The stored document with generated id and timestamps
 */
function add(doc) {
  const store = load();
  const id = generateId();
  const timestamp = now();

  const record = {
    id,
    content: doc.content || '',
    metadata: {
      source: null,
      tags: [],
      ...(doc.metadata || {}),
      createdAt: timestamp,
      updatedAt: timestamp,
    },
    embedding: doc.embedding || null,
  };

  store.documents[id] = record;
  markDirty();
  return record;
}

/**
 * Get a document by id
 * @param {string} id
 * @returns {object|null}
 */
function get(id) {
  const store = load();
  return store.documents[id] || null;
}

/**
 * Update a document (partial merge)
 * @param {string} id
 * @param {object} updates - { content?, metadata?, embedding? }
 * @returns {object|null} Updated document or null if not found
 */
function update(id, updates) {
  const store = load();
  const existing = store.documents[id];
  if (!existing) return null;

  if (updates.content !== undefined) {
    existing.content = updates.content;
  }
  if (updates.metadata) {
    existing.metadata = { ...existing.metadata, ...updates.metadata };
  }
  if (updates.embedding !== undefined) {
    existing.embedding = updates.embedding;
  }
  existing.metadata.updatedAt = now();

  markDirty();
  return existing;
}

/**
 * Delete a document by id
 * @param {string} id
 * @returns {boolean} Whether the document existed
 */
function remove(id) {
  const store = load();
  if (!store.documents[id]) return false;
  delete store.documents[id];
  markDirty();
  return true;
}

/**
 * List all documents (lightweight — omits embeddings by default)
 * @param {object} opts - { includeEmbeddings?: boolean }
 * @returns {object[]}
 */
function listAll(opts = {}) {
  const store = load();
  return Object.values(store.documents).map(doc => {
    if (opts.includeEmbeddings) return { ...doc };
    const { embedding, ...rest } = doc;
    return rest;
  });
}

/**
 * Search documents by metadata tags
 * @param {string[]} tags - Tags to match (AND logic)
 * @returns {object[]} Matching documents (without embeddings)
 */
function searchByTags(tags) {
  const store = load();
  const tagSet = new Set(tags.map(t => t.toLowerCase()));
  return Object.values(store.documents).filter(doc => {
    const docTags = (doc.metadata.tags || []).map(t => t.toLowerCase());
    return [...tagSet].every(tag => docTags.includes(tag));
  }).map(({ embedding, ...rest }) => rest);
}

/**
 * Get all documents with embeddings (for search operations)
 * @returns {object[]}
 */
function getAllWithEmbeddings() {
  const store = load();
  return Object.values(store.documents).filter(doc => doc.embedding !== null);
}

/**
 * Get store statistics
 * @returns {object}
 */
function stats() {
  const store = load();
  const docs = Object.values(store.documents);
  const withEmbeddings = docs.filter(d => d.embedding !== null).length;
  let diskBytes = 0;
  try {
    const stat = fs.statSync(STORE_PATH);
    diskBytes = stat.size;
  } catch (_) {}

  let lastIndexed = null;
  for (const doc of docs) {
    const ts = doc.metadata.createdAt;
    if (ts && (!lastIndexed || ts > lastIndexed)) {
      lastIndexed = ts;
    }
  }

  return {
    totalDocuments: docs.length,
    withEmbeddings,
    diskBytes,
    diskSize: formatBytes(diskBytes),
    lastIndexed,
    storePath: STORE_PATH,
  };
}

/**
 * Force flush to disk immediately
 */
function flush() {
  if (_flushTimer) {
    clearTimeout(_flushTimer);
    _flushTimer = null;
  }
  if (_dirty) save();
}

/**
 * Clear all documents (destructive!)
 */
function clear() {
  const store = load();
  store.documents = {};
  markDirty();
  flush();
}

/**
 * Reload from disk (discard in-memory changes)
 */
function reload() {
  _store = null;
  _dirty = false;
  if (_flushTimer) {
    clearTimeout(_flushTimer);
    _flushTimer = null;
  }
  load();
}

// ── Utilities ───────────────────────────────────────────────────────

function formatBytes(bytes) {
  if (bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return (bytes / Math.pow(1024, i)).toFixed(1) + ' ' + units[i];
}

// ── Cleanup on exit ─────────────────────────────────────────────────

process.on('exit', () => {
  if (_dirty) {
    try { save(); } catch (_) {}
  }
});

module.exports = {
  add,
  get,
  update,
  remove,
  delete: remove,  // alias
  listAll,
  searchByTags,
  getAllWithEmbeddings,
  stats,
  flush,
  clear,
  reload,
  STORE_PATH,
  NOVA_DIR,
};
