'use strict';

/**
 * Nova Embedder — Local embeddings via Ollama
 * 
 * Primary: Ollama /api/embeddings with nomic-embed-text
 * Fallback: Simple TF-IDF-like word frequency vectors
 */

const http = require('http');
const https = require('https');
const url = require('url');

const DEFAULT_OLLAMA_URL = process.env.NOVA_OLLAMA_URL || 'http://localhost:11434';
const DEFAULT_MODEL = process.env.NOVA_EMBED_MODEL || 'nomic-embed-text';

let _ollamaAvailable = null; // null = unknown, true/false = tested

// ── Ollama HTTP Client ──────────────────────────────────────────────

/**
 * Make an HTTP request to Ollama
 * @param {string} endpoint - e.g. '/api/embeddings'
 * @param {object} body
 * @param {object} opts - { baseUrl?, timeoutMs? }
 * @returns {Promise<object>}
 */
function ollamaRequest(endpoint, body, opts = {}) {
  const baseUrl = opts.baseUrl || DEFAULT_OLLAMA_URL;
  const timeoutMs = opts.timeoutMs || 30000;

  return new Promise((resolve, reject) => {
    const parsed = new URL(endpoint, baseUrl);
    const transport = parsed.protocol === 'https:' ? https : http;
    const payload = JSON.stringify(body);

    const reqOpts = {
      hostname: parsed.hostname,
      port: parsed.port,
      path: parsed.pathname,
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(payload),
      },
      timeout: timeoutMs,
    };

    const req = transport.request(reqOpts, (res) => {
      let data = '';
      res.on('data', chunk => { data += chunk; });
      res.on('end', () => {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          try {
            resolve(JSON.parse(data));
          } catch (err) {
            reject(new Error(`Invalid JSON from Ollama: ${data.slice(0, 200)}`));
          }
        } else {
          reject(new Error(`Ollama ${res.statusCode}: ${data.slice(0, 500)}`));
        }
      });
    });

    req.on('error', reject);
    req.on('timeout', () => {
      req.destroy();
      reject(new Error('Ollama request timed out'));
    });

    req.write(payload);
    req.end();
  });
}

/**
 * Check if Ollama is available (with caching)
 * @returns {Promise<boolean>}
 */
async function isOllamaAvailable() {
  if (_ollamaAvailable !== null) return _ollamaAvailable;

  return new Promise((resolve) => {
    const parsed = new URL(DEFAULT_OLLAMA_URL);
    const transport = parsed.protocol === 'https:' ? https : http;

    const req = transport.get(`${DEFAULT_OLLAMA_URL}/api/tags`, { timeout: 3000 }, (res) => {
      let data = '';
      res.on('data', chunk => { data += chunk; });
      res.on('end', () => {
        _ollamaAvailable = res.statusCode === 200;
        resolve(_ollamaAvailable);
      });
    });

    req.on('error', () => {
      _ollamaAvailable = false;
      resolve(false);
    });

    req.on('timeout', () => {
      req.destroy();
      _ollamaAvailable = false;
      resolve(false);
    });
  });
}

/**
 * Reset the Ollama availability cache
 */
function resetCache() {
  _ollamaAvailable = null;
}

// ── Ollama Embeddings ───────────────────────────────────────────────

/**
 * Generate embedding for a single text via Ollama
 * @param {string} text
 * @param {object} opts - { model?, baseUrl? }
 * @returns {Promise<number[]>} Float array embedding
 */
async function embedOllama(text, opts = {}) {
  const model = opts.model || DEFAULT_MODEL;
  const response = await ollamaRequest('/api/embeddings', {
    model,
    prompt: text,
  }, opts);

  if (!response.embedding || !Array.isArray(response.embedding)) {
    throw new Error('Invalid embedding response from Ollama');
  }

  return response.embedding;
}

// ── Fallback: TF-IDF-like Word Frequency Vector ─────────────────────

// Fixed vocabulary for consistent vector dimensions
const VOCAB_SIZE = 512;

/**
 * Simple hash function for mapping words to vector positions
 */
function hashWord(word) {
  let hash = 0;
  for (let i = 0; i < word.length; i++) {
    hash = ((hash << 5) - hash + word.charCodeAt(i)) | 0;
  }
  return ((hash % VOCAB_SIZE) + VOCAB_SIZE) % VOCAB_SIZE;
}

/**
 * Tokenize text into normalized words
 */
function tokenize(text) {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, ' ')
    .split(/\s+/)
    .filter(w => w.length > 1 && w.length < 30);
}

/**
 * Generate a TF-IDF-like word frequency vector (fallback)
 * @param {string} text
 * @returns {number[]}
 */
function embedFallback(text) {
  const tokens = tokenize(text);
  if (tokens.length === 0) return new Array(VOCAB_SIZE).fill(0);

  // Build term frequency vector
  const vec = new Array(VOCAB_SIZE).fill(0);
  for (const token of tokens) {
    const idx = hashWord(token);
    vec[idx] += 1;
  }

  // Normalize: L2 norm
  let norm = 0;
  for (let i = 0; i < vec.length; i++) {
    norm += vec[i] * vec[i];
  }
  norm = Math.sqrt(norm);
  if (norm > 0) {
    for (let i = 0; i < vec.length; i++) {
      vec[i] /= norm;
    }
  }

  return vec;
}

// ── Public API ──────────────────────────────────────────────────────

/**
 * Generate embedding for text — tries Ollama first, falls back to word frequency
 * @param {string} text
 * @param {object} opts - { model?, baseUrl?, forceFallback? }
 * @returns {Promise<{ embedding: number[], source: 'ollama'|'fallback' }>}
 */
async function embed(text, opts = {}) {
  if (opts.forceFallback) {
    return { embedding: embedFallback(text), source: 'fallback' };
  }

  const available = await isOllamaAvailable();
  if (available) {
    try {
      const embedding = await embedOllama(text, opts);
      return { embedding, source: 'ollama' };
    } catch (err) {
      console.warn('[embedder] Ollama embedding failed, using fallback:', err.message);
      return { embedding: embedFallback(text), source: 'fallback' };
    }
  }

  return { embedding: embedFallback(text), source: 'fallback' };
}

/**
 * Generate embeddings for multiple texts
 * @param {string[]} texts
 * @param {object} opts
 * @returns {Promise<{ embeddings: number[][], source: 'ollama'|'fallback' }>}
 */
async function embedBatch(texts, opts = {}) {
  if (opts.forceFallback) {
    return {
      embeddings: texts.map(t => embedFallback(t)),
      source: 'fallback',
    };
  }

  const available = await isOllamaAvailable();
  if (available && !opts.forceFallback) {
    try {
      // Ollama doesn't have a native batch endpoint, so we parallelize
      // with concurrency limit to avoid overwhelming it
      const CONCURRENCY = 4;
      const results = new Array(texts.length);
      let idx = 0;

      async function worker() {
        while (idx < texts.length) {
          const i = idx++;
          results[i] = await embedOllama(texts[i], opts);
        }
      }

      const workers = [];
      for (let w = 0; w < Math.min(CONCURRENCY, texts.length); w++) {
        workers.push(worker());
      }
      await Promise.all(workers);

      return { embeddings: results, source: 'ollama' };
    } catch (err) {
      console.warn('[embedder] Ollama batch failed, using fallback:', err.message);
    }
  }

  return {
    embeddings: texts.map(t => embedFallback(t)),
    source: 'fallback',
  };
}

module.exports = {
  embed,
  embedBatch,
  embedFallback,
  isOllamaAvailable,
  resetCache,
  DEFAULT_MODEL,
  DEFAULT_OLLAMA_URL,
  VOCAB_SIZE,
};
