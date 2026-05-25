'use strict';

const https = require('https');
const { BaseSkill } = require('../base-skill');

/**
 * WebSearchSkill — searches the web using DuckDuckGo HTML endpoint.
 * Returns top 5 results with title, URL, and snippet.
 */
class WebSearchSkill extends BaseSkill {
  constructor() {
    super({
      name: 'web_search',
      description: 'Search the web using DuckDuckGo. Returns top 5 results with title, URL, and snippet.',
      version: '1.0.0',
      category: 'search',
      parameters: {
        type: 'object',
        properties: {
          query: {
            type: 'string',
            description: 'The search query string',
          },
        },
        required: ['query'],
      },
    });
  }

  /**
   * Fetch a URL over HTTPS and return the response body as a string.
   * Follows up to 5 redirects.
   * @param {string} url
   * @param {number} [maxRedirects=5]
   * @returns {Promise<string>}
   * @private
   */
  _fetch(url, maxRedirects = 5) {
    return new Promise((resolve, reject) => {
      const req = https.get(url, {
        headers: {
          'User-Agent': 'Mozilla/5.0 (compatible; NovaBot/1.0)',
          'Accept': 'text/html',
        },
        timeout: 15000,
      }, (res) => {
        // Handle redirects
        if ([301, 302, 303, 307, 308].includes(res.statusCode) && res.headers.location) {
          if (maxRedirects <= 0) {
            return reject(new Error('Too many redirects'));
          }
          const redirectUrl = res.headers.location.startsWith('http')
            ? res.headers.location
            : new URL(res.headers.location, url).href;
          return resolve(this._fetch(redirectUrl, maxRedirects - 1));
        }

        if (res.statusCode !== 200) {
          return reject(new Error(`HTTP ${res.statusCode}`));
        }

        const chunks = [];
        res.on('data', (chunk) => chunks.push(chunk));
        res.on('end', () => resolve(Buffer.concat(chunks).toString('utf-8')));
        res.on('error', reject);
      });

      req.on('timeout', () => { req.destroy(); reject(new Error('Request timed out')); });
      req.on('error', reject);
    });
  }

  /**
   * Parse DuckDuckGo HTML results page for result entries.
   * @param {string} html
   * @returns {{title: string, url: string, snippet: string}[]}
   * @private
   */
  _parseResults(html) {
    const results = [];

    // DuckDuckGo HTML results are in <a class="result__a" ...> for titles/links
    // and <a class="result__snippet" ...> for snippets
    const resultBlocks = html.split(/class="result\s/g);

    for (let i = 1; i < resultBlocks.length && results.length < 5; i++) {
      const block = resultBlocks[i];

      // Extract title and URL from result__a
      const titleMatch = block.match(/class="result__a"[^>]*href="([^"]*)"[^>]*>([\s\S]*?)<\/a>/);
      if (!titleMatch) continue;

      let url = titleMatch[1];
      const titleHtml = titleMatch[2];

      // DuckDuckGo wraps URLs in a redirect — extract the actual URL
      const uddgMatch = url.match(/uddg=([^&]+)/);
      if (uddgMatch) {
        url = decodeURIComponent(uddgMatch[1]);
      }

      // Strip HTML tags from title
      const title = titleHtml.replace(/<[^>]*>/g, '').replace(/&amp;/g, '&')
        .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"')
        .replace(/&#x27;/g, "'").replace(/&#39;/g, "'").trim();

      // Extract snippet
      const snippetMatch = block.match(/class="result__snippet"[^>]*>([\s\S]*?)<\/a>/);
      const snippet = snippetMatch
        ? snippetMatch[1].replace(/<[^>]*>/g, '').replace(/&amp;/g, '&')
          .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"')
          .replace(/&#x27;/g, "'").replace(/&#39;/g, "'").trim()
        : '';

      if (title && url) {
        results.push({ title, url, snippet });
      }
    }

    return results;
  }

  /**
   * Execute the web search.
   * @param {{query: string}} args
   * @returns {Promise<{results: {title: string, url: string, snippet: string}[]}>}
   */
  async execute(args) {
    if (!args || !args.query || typeof args.query !== 'string') {
      throw new Error('Parameter "query" is required and must be a string');
    }

    const query = encodeURIComponent(args.query.trim());
    const url = `https://html.duckduckgo.com/html/?q=${query}`;

    try {
      const html = await this._fetch(url);
      const results = this._parseResults(html);

      if (results.length === 0) {
        return { results: [], message: 'No results found.' };
      }

      return { results };
    } catch (err) {
      throw new Error(`Web search failed: ${err.message}`);
    }
  }
}

module.exports = new WebSearchSkill();
