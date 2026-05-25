/**
 * @module digest
 * @description Morning Digest Agent — gathers date/time, weather, system info,
 * and generates a briefing via Ollama. Supports TTS output for macOS `say`.
 */

'use strict';

const BaseAgent = require('./base-agent');
const http = require('http');
const https = require('https');
const os = require('os');
const { execFile } = require('child_process');

const DIGEST_SYSTEM = `You are Nova Digest Agent. You create concise, informative morning briefings.

## Style
- Friendly but efficient — like a smart assistant giving a quick brief
- Lead with the most important info
- Use bullet points for scanability
- Include a motivational note or fun fact at the end
- Keep it under 300 words

## Format
Structure your briefing like this:
1. Good morning greeting with date/time
2. Weather summary
3. System status highlights
4. Any notable events or reminders
5. Brief motivational closing
`;

/**
 * @typedef {Object} DigestData
 * @property {string} dateTime - Current date/time string
 * @property {string} dayOfWeek - Day name
 * @property {Object} weather - Weather data
 * @property {Object} system - System information
 * @property {Object} [custom] - Custom data sources
 */

/**
 * @typedef {Object} DigestResult
 * @property {string} briefing - The full briefing text
 * @property {string} ttsText - Clean text for TTS (no markdown)
 * @property {DigestData} data - Raw data used to generate the digest
 * @property {string} greeting - Short greeting line
 */

/**
 * Morning Digest agent — gathers contextual data and generates a briefing.
 *
 * @extends BaseAgent
 */
class DigestAgent extends BaseAgent {
  /**
   * @param {Object} [config={}]
   * @param {string} [config.location='auto'] - Location for weather (city name or 'auto')
   * @param {boolean} [config.tts=true] - Include TTS-ready text
   * @param {Function[]} [config.customSources] - Additional async data source functions
   */
  constructor(config = {}) {
    super('digest', 'scheduled', {
      systemPrompt: DIGEST_SYSTEM,
      temperature: 0.6, // Slightly creative for briefings
      ...config,
    });

    /** @type {string} */
    this.location = config.location || 'auto';

    /** @type {boolean} */
    this.tts = config.tts !== false;

    /** @type {Function[]} */
    this.customSources = config.customSources || [];
  }

  /**
   * Generate a morning digest.
   *
   * @param {string} [input] - Optional focus / override instructions
   * @param {Object} [context={}]
   * @param {string} [context.location] - Override weather location
   * @returns {Promise<import('./base-agent').AgentResult>}
   */
  async run(input, context = {}) {
    this._prepare();

    try {
      const location = context.location || this.location;

      // Gather all data sources in parallel
      this._addStep('gather', 'Collecting data sources');

      const [dateTime, weather, system, ...customResults] = await Promise.all([
        this._getDateTime(),
        this._getWeather(location),
        this._getSystemInfo(),
        ...this.customSources.map((fn) => this._safeCall(fn)),
      ]);

      /** @type {DigestData} */
      const data = {
        dateTime: dateTime.formatted,
        dayOfWeek: dateTime.dayOfWeek,
        weather,
        system,
        custom: {},
      };

      // Merge custom source results
      customResults.forEach((result, i) => {
        if (result) {
          data.custom[`source_${i}`] = result;
        }
      });

      this._addStep('data-collected', `Weather: ${weather.summary || 'unavailable'}, Uptime: ${system.uptime}`);

      // Build the prompt
      const dataStr = this._formatDataForLLM(data, input);

      // Generate the briefing
      this._addStep('generate', 'Generating briefing via Ollama');
      const briefing = await this.think(dataStr);

      // Create TTS-clean version
      const ttsText = this.tts ? this._cleanForTTS(briefing) : '';

      // Extract greeting (first line)
      const greeting = briefing.split('\n').find((l) => l.trim()) || 'Good morning!';

      /** @type {DigestResult} */
      const digest = {
        briefing,
        ttsText,
        data,
        greeting,
      };

      return this._result(briefing, {
        digest,
        hasTTS: this.tts,
        location,
        dataSourceCount: 3 + this.customSources.length,
      });
    } catch (err) {
      return this._error(err);
    } finally {
      this._cleanup();
    }
  }

  // ── Data Sources ──────────────────────────────────────────────────

  /**
   * Get current date/time info.
   *
   * @returns {Promise<{formatted: string, dayOfWeek: string, date: string, time: string, timezone: string}>}
   * @private
   */
  async _getDateTime() {
    const now = new Date();
    const days = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
    const months = ['January', 'February', 'March', 'April', 'May', 'June',
      'July', 'August', 'September', 'October', 'November', 'December'];

    return {
      formatted: now.toLocaleString('en-US', {
        weekday: 'long', year: 'numeric', month: 'long', day: 'numeric',
        hour: '2-digit', minute: '2-digit', timeZoneName: 'short',
      }),
      dayOfWeek: days[now.getDay()],
      date: `${months[now.getMonth()]} ${now.getDate()}, ${now.getFullYear()}`,
      time: now.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }),
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    };
  }

  /**
   * Fetch weather from wttr.in.
   *
   * @param {string} location
   * @returns {Promise<{summary: string, temperature: string, condition: string, raw: string}>}
   * @private
   */
  async _getWeather(location) {
    try {
      const loc = location === 'auto' ? '' : encodeURIComponent(location);
      const url = `https://wttr.in/${loc}?format=j1`;

      const data = await this._httpGet(url);
      const parsed = JSON.parse(data);

      const current = parsed.current_condition?.[0] || {};
      const area = parsed.nearest_area?.[0] || {};

      const city = area.areaName?.[0]?.value || location;
      const tempF = current.temp_F || '?';
      const tempC = current.temp_C || '?';
      const condition = current.weatherDesc?.[0]?.value || 'Unknown';
      const humidity = current.humidity || '?';
      const windMph = current.windspeedMiles || '?';

      return {
        summary: `${city}: ${tempF}°F (${tempC}°C), ${condition}`,
        temperature: `${tempF}°F / ${tempC}°C`,
        condition,
        humidity: `${humidity}%`,
        wind: `${windMph} mph`,
        city,
        raw: JSON.stringify(current).slice(0, 500),
      };
    } catch (err) {
      return {
        summary: 'Weather unavailable',
        temperature: '?',
        condition: 'unavailable',
        raw: err.message,
      };
    }
  }

  /**
   * Get system information.
   *
   * @returns {Promise<Object>}
   * @private
   */
  async _getSystemInfo() {
    const totalMem = os.totalmem();
    const freeMem = os.freemem();
    const usedMem = totalMem - freeMem;
    const uptimeSeconds = os.uptime();

    const hours = Math.floor(uptimeSeconds / 3600);
    const mins = Math.floor((uptimeSeconds % 3600) / 60);

    let diskInfo = 'unknown';
    try {
      diskInfo = await this._execCommand('df', ['-h', '/']);
    } catch (_) {}

    return {
      platform: `${os.type()} ${os.release()} (${os.arch()})`,
      hostname: os.hostname(),
      uptime: `${hours}h ${mins}m`,
      memory: {
        total: `${(totalMem / 1e9).toFixed(1)} GB`,
        used: `${(usedMem / 1e9).toFixed(1)} GB`,
        free: `${(freeMem / 1e9).toFixed(1)} GB`,
        percent: `${((usedMem / totalMem) * 100).toFixed(0)}%`,
      },
      cpus: os.cpus().length,
      loadAvg: os.loadavg().map((l) => l.toFixed(2)),
      disk: diskInfo.split('\n').slice(0, 2).join(' | '),
    };
  }

  // ── Helpers ───────────────────────────────────────────────────────

  /**
   * Format collected data into an LLM prompt.
   *
   * @param {DigestData} data
   * @param {string} [focus]
   * @returns {string}
   * @private
   */
  _formatDataForLLM(data, focus) {
    let prompt = `Generate a morning briefing using this data:

## Date & Time
${data.dateTime} (${data.dayOfWeek})

## Weather
${typeof data.weather === 'object' ? JSON.stringify(data.weather, null, 2) : data.weather}

## System
${typeof data.system === 'object' ? JSON.stringify(data.system, null, 2) : data.system}`;

    if (data.custom && Object.keys(data.custom).length > 0) {
      prompt += `\n\n## Additional Info\n${JSON.stringify(data.custom, null, 2)}`;
    }

    if (focus) {
      prompt += `\n\n## Special Focus\n${focus}`;
    }

    return prompt;
  }

  /**
   * Clean markdown/formatting for TTS output.
   *
   * @param {string} text
   * @returns {string}
   * @private
   */
  _cleanForTTS(text) {
    return text
      .replace(/#{1,6}\s*/g, '')           // Remove headers
      .replace(/\*{1,2}(.*?)\*{1,2}/g, '$1') // Remove bold/italic
      .replace(/`[^`]*`/g, '')              // Remove code
      .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1') // Links → text
      .replace(/[-*+]\s+/g, '. ')          // Bullets → periods
      .replace(/\n{2,}/g, '. ')            // Double newlines → period
      .replace(/\n/g, ' ')                 // Single newlines → space
      .replace(/\s{2,}/g, ' ')             // Collapse whitespace
      .trim();
  }

  /**
   * Make an HTTPS GET request and return the body as a string.
   *
   * @param {string} url
   * @returns {Promise<string>}
   * @private
   */
  _httpGet(url) {
    return new Promise((resolve, reject) => {
      const client = url.startsWith('https') ? https : http;
      const req = client.get(url, { timeout: 10000 }, (res) => {
        // Follow redirects
        if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
          this._httpGet(res.headers.location).then(resolve).catch(reject);
          return;
        }

        let data = '';
        res.on('data', (chunk) => { data += chunk; });
        res.on('end', () => resolve(data));
        res.on('error', reject);
      });

      req.on('timeout', () => { req.destroy(); reject(new Error('HTTP timeout')); });
      req.on('error', reject);
    });
  }

  /**
   * Execute a command and return stdout.
   *
   * @param {string} cmd
   * @param {string[]} args
   * @returns {Promise<string>}
   * @private
   */
  _execCommand(cmd, args) {
    return new Promise((resolve, reject) => {
      execFile(cmd, args, { timeout: 5000 }, (err, stdout) => {
        if (err) reject(err);
        else resolve(stdout || '');
      });
    });
  }

  /**
   * Safely call an async function, returning null on error.
   *
   * @param {Function} fn
   * @returns {Promise<any|null>}
   * @private
   */
  async _safeCall(fn) {
    try {
      return await fn();
    } catch (_) {
      return null;
    }
  }
}

/** @type {string} */
DigestAgent.agentType = 'digest';

/** @type {string} */
DigestAgent.description = 'Morning digest agent — gathers weather, system info, and generates a briefing with TTS support';

/** @type {string} */
DigestAgent.category = 'scheduled';

module.exports = DigestAgent;
