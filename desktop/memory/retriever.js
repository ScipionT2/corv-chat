'use strict';

/**
 * Nova Retriever — Semantic search over stored documents
 * 
 * Uses cosine similarity between query embeddings and stored document embeddings.
 * Supports metadata filtering and relevance scoring.
 */

const store = require('./store');
const embedder = require('./embedder');

// ── Vector Math ─────────────────────────────────────────────────────

/**
 * Compute cosine similarity between two vectors
 * @param {number[]} a
 * @param {number[]} b
 * @returns {number} Similarity score in [-1, 1]
 */
function cosineSimilarity(a, b) {
  if (!a || !b || a.length !== b.length) return 0;

  let dotProduct = 0;
  let normA = 0;
  let normB = 0;

  for (let i = 0; i < a.length; i++) {
    dotProduct += a[i] * b[i];
    normA += a[i] * a[i];
    normB += b[i] * b[i];
  }

  const denominator = Math.sqrt(normA) * Math.sqrt(normB);
  if (denominator === 0) return 0;

  return dotProduct / denominator;
}

/**
 * Check if a document matches metadata filters
 * @param {object} doc - Document with metadata
 * @param {object} filters - Metadata filters to apply
 * @returns {boolean}
 */
function matchesFilters(doc, filters) {
  if (!filters) return true;

  const meta = doc.metadata || {};

  // Tag filter (AND logic — all specified tags must be present)
  if (filters.tags && filters.tags.length > 0) {
    const docTags = (meta.tags || []).map(t => t.toLowerCase());
    const filterTags = filters.tags.map(t => t.toLowerCase());
    if (!filterTags.every(tag => docTags.includes(tag))) return false;
  }

  // Source filter
  if (filters.source) {
    if (meta.source !== filters.source) return false;
  }

  // Date range filter
  if (filters.after) {
    const afterDate = new Date(filters.after);
    const docDate = new Date(meta.createdAt);
    if (docDate < afterDate) return false;
  }
  if (filters.before) {
    const beforeDate = new Date(filters.before);
    const docDate = new Date(meta.createdAt);
    if (docDate > beforeDate) return false;
  }

  // Custom key-value filters
  if (filters.custom) {
    for (const [key, value] of Object.entries(filters.custom)) {
      if (meta[key] !== value) return false;
    }
  }

  return true;
}

// ── Search ──────────────────────────────────────────────────────────

/**
 * Semantic search across stored memories
 * 
 * @param {string} query - Natural language search query
 * @param {object} opts - Search options
 * @param {number} opts.topK - Number of results (default: 5)
 * @param {number} opts.minScore - Minimum similarity threshold (default: 0.0)
 * @param {object} opts.filters - Metadata filters { tags?, source?, after?, before?, custom? }
 * @param {boolean} opts.includeEmbeddings - Include embedding vectors in results
 * @returns {Promise<object[]>} Results sorted by relevance, each with { doc, score }
 */
async function search(query, opts = {}) {
  const topK = opts.topK || 5;
  const minScore = opts.minScore || 0.0;
  const filters = opts.filters || null;

  // Get query embedding
  const { embedding: queryEmbedding, source: embedSource } = await embedder.embed(query);

  // Get all documents with embeddings
  const documents = store.getAllWithEmbeddings();

  // Score and filter
  const scored = [];
  for (const doc of documents) {
    // Apply metadata filters first (cheap)
    if (!matchesFilters(doc, filters)) continue;

    // Check embedding compatibility
    // (Ollama embeddings and fallback embeddings have different dimensions,
    //  so we skip docs with incompatible embedding sizes)
    if (doc.embedding.length !== queryEmbedding.length) continue;

    // Compute similarity
    const score = cosineSimilarity(queryEmbedding, doc.embedding);
    if (score >= minScore) {
      scored.push({ doc, score });
    }
  }

  // Sort by score descending
  scored.sort((a, b) => b.score - a.score);

  // Take top K
  const results = scored.slice(0, topK);

  // Strip embeddings from results unless requested
  if (!opts.includeEmbeddings) {
    return results.map(({ doc, score }) => {
      const { embedding, ...rest } = doc;
      return { doc: rest, score, embedSource };
    });
  }

  return results.map(r => ({ ...r, embedSource }));
}

/**
 * Find documents similar to a given document ID
 * @param {string} docId - ID of the reference document
 * @param {object} opts - Same as search opts
 * @returns {Promise<object[]>}
 */
async function findSimilar(docId, opts = {}) {
  const topK = opts.topK || 5;
  const refDoc = store.get(docId);
  if (!refDoc || !refDoc.embedding) {
    throw new Error(`Document ${docId} not found or has no embedding`);
  }

  const documents = store.getAllWithEmbeddings();
  const scored = [];

  for (const doc of documents) {
    if (doc.id === docId) continue; // Skip self
    if (doc.embedding.length !== refDoc.embedding.length) continue;
    if (opts.filters && !matchesFilters(doc, opts.filters)) continue;

    const score = cosineSimilarity(refDoc.embedding, doc.embedding);
    if (score >= (opts.minScore || 0)) {
      scored.push({ doc, score });
    }
  }

  scored.sort((a, b) => b.score - a.score);

  return scored.slice(0, topK).map(({ doc, score }) => {
    if (opts.includeEmbeddings) return { doc, score };
    const { embedding, ...rest } = doc;
    return { doc: rest, score };
  });
}

module.exports = {
  search,
  findSimilar,
  cosineSimilarity,
};
