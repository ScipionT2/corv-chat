'use strict';

const https = require('https');
const { BaseSkill } = require('../base-skill');

/**
 * Fetch JSON from a URL using Node.js built-in https.
 * @param {string} url
 * @param {number} timeout
 * @returns {Promise<object>}
 */
function fetchJSON(url, timeout = 10000) {
  return new Promise((resolve, reject) => {
    const req = https.get(url, { timeout, headers: { 'User-Agent': 'Nova-AI/2.0' } }, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        return fetchJSON(res.headers.location, timeout).then(resolve, reject);
      }
      if (res.statusCode !== 200) {
        res.resume();
        return reject(new Error(`HTTP ${res.statusCode}`));
      }
      let data = '';
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => {
        try { resolve(JSON.parse(data)); }
        catch (e) { reject(new Error('Invalid JSON from wttr.in')); }
      });
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('Request timed out')); });
  });
}

/**
 * Get current weather for a location via wttr.in.
 */
class GetWeatherSkill extends BaseSkill {
  constructor() {
    super({
      name: 'get_weather',
      description: 'Get current weather conditions for a location. Returns temperature, condition, humidity, wind speed, and more.',
      version: '1.0.0',
      category: 'information',
      parameters: {
        type: 'object',
        properties: {
          location: {
            type: 'string',
            description: 'City name, zip code, or coordinates (e.g. "San Francisco", "90210", "48.8566,2.3522")',
          },
        },
        required: ['location'],
      },
    });
  }

  async execute(args) {
    const { location } = args;
    if (!location || typeof location !== 'string') {
      return { ok: false, error: 'Location is required' };
    }

    try {
      const encoded = encodeURIComponent(location.trim());
      const data = await fetchJSON(`https://wttr.in/${encoded}?format=j1`);

      const current = data.current_condition?.[0];
      if (!current) {
        return { ok: false, error: 'No weather data available for this location' };
      }

      const area = data.nearest_area?.[0];
      const locationName = area
        ? `${area.areaName?.[0]?.value || ''}, ${area.region?.[0]?.value || ''}, ${area.country?.[0]?.value || ''}`
        : location;

      return {
        ok: true,
        location: locationName.replace(/, ,/g, ',').replace(/^, |, $/g, ''),
        temperature: {
          celsius: parseInt(current.temp_C, 10),
          fahrenheit: parseInt(current.temp_F, 10),
          feelsLike_C: parseInt(current.FeelsLikeC, 10),
          feelsLike_F: parseInt(current.FeelsLikeF, 10),
        },
        condition: current.weatherDesc?.[0]?.value || 'Unknown',
        humidity: parseInt(current.humidity, 10),
        wind: {
          speed_mph: parseInt(current.windspeedMiles, 10),
          speed_kmh: parseInt(current.windspeedKmph, 10),
          direction: current.winddir16Point || '',
        },
        visibility_km: parseInt(current.visibility, 10),
        uv_index: parseInt(current.uvIndex, 10),
        pressure_mb: parseInt(current.pressure, 10),
        cloud_cover: parseInt(current.cloudcover, 10),
        observation_time: current.observation_time || '',
      };
    } catch (err) {
      return { ok: false, error: `Weather fetch failed: ${err.message}` };
    }
  }
}

/**
 * Get weather forecast for a location via wttr.in.
 */
class GetForecastSkill extends BaseSkill {
  constructor() {
    super({
      name: 'get_forecast',
      description: 'Get weather forecast for a location. Returns daily forecasts with high/low temps, conditions, and hourly breakdowns.',
      version: '1.0.0',
      category: 'information',
      parameters: {
        type: 'object',
        properties: {
          location: {
            type: 'string',
            description: 'City name, zip code, or coordinates',
          },
          days: {
            type: 'number',
            description: 'Number of forecast days (1-3, default 3)',
          },
        },
        required: ['location'],
      },
    });
  }

  async execute(args) {
    const { location, days = 3 } = args;
    if (!location || typeof location !== 'string') {
      return { ok: false, error: 'Location is required' };
    }

    const numDays = Math.min(3, Math.max(1, parseInt(days, 10) || 3));

    try {
      const encoded = encodeURIComponent(location.trim());
      const data = await fetchJSON(`https://wttr.in/${encoded}?format=j1`);

      const weather = data.weather;
      if (!weather || !weather.length) {
        return { ok: false, error: 'No forecast data available for this location' };
      }

      const area = data.nearest_area?.[0];
      const locationName = area
        ? `${area.areaName?.[0]?.value || ''}, ${area.region?.[0]?.value || ''}, ${area.country?.[0]?.value || ''}`
        : location;

      const forecast = weather.slice(0, numDays).map((day) => ({
        date: day.date,
        maxTemp_C: parseInt(day.maxtempC, 10),
        maxTemp_F: parseInt(day.maxtempF, 10),
        minTemp_C: parseInt(day.mintempC, 10),
        minTemp_F: parseInt(day.mintempF, 10),
        avgTemp_C: parseInt(day.avgtempC, 10),
        avgTemp_F: parseInt(day.avgtempF, 10),
        sun_hours: parseFloat(day.sunHour) || 0,
        uv_index: parseInt(day.uvIndex, 10),
        hourly: (day.hourly || []).map((h) => ({
          time: `${String(Math.floor(parseInt(h.time, 10) / 100)).padStart(2, '0')}:00`,
          temp_C: parseInt(h.tempC, 10),
          temp_F: parseInt(h.tempF, 10),
          condition: h.weatherDesc?.[0]?.value || '',
          rain_chance: parseInt(h.chanceofrain, 10),
          wind_kmh: parseInt(h.windspeedKmph, 10),
          humidity: parseInt(h.humidity, 10),
        })),
      }));

      return {
        ok: true,
        location: locationName.replace(/, ,/g, ',').replace(/^, |, $/g, ''),
        days: numDays,
        forecast,
      };
    } catch (err) {
      return { ok: false, error: `Forecast fetch failed: ${err.message}` };
    }
  }
}

module.exports = {
  skills: [
    new GetWeatherSkill(),
    new GetForecastSkill(),
  ],
};
