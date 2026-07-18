const https = require('https');
const fs = require('fs');
const path = require('path');
const { getStore } = require('@netlify/blobs');

// ══════════════════════════════════════
// ⚠️ ВРЕМЕННАЯ ТЕСТОВАЯ ВЕРСИЯ ⚠️
// Цель: проверить гипотезу, что демо-заглушка показывается из-за
// таймаута Netlify (10 сек по умолчанию), а не из-за другой ошибки.
// Здесь урезан max_tokens и упрощён промпт, чтобы Claude отвечал
// быстрее (в идеале — за 3-7 секунд вместо 30-35).
// ПОСЛЕ ТЕСТА ВЕРНУТЬ ОБЫЧНУЮ ВЕРСИЮ planner.js — это НЕ финальный код.
// ══════════════════════════════════════

let _warningsCache = null;
let _warningsCacheTime = 0;
const WARNINGS_TTL_MS = 5 * 60 * 1000;

function loadWarnings() {
  const now = Date.now();
  if (_warningsCache && (now - _warningsCacheTime) < WARNINGS_TTL_MS) {
    return _warningsCache;
  }
  try {
    const filePath = path.join(__dirname, '..', '..', 'planner', 'warnings.json');
    const raw = fs.readFileSync(filePath, 'utf-8');
    _warningsCache = JSON.parse(raw);
    _warningsCacheTime = now;
    return _warningsCache;
  } catch (e) {
    console.warn('Не удалось прочитать warnings.json:', e.message);
    return null;
  }
}

const requestLog = {};
const RATE_LIMIT = 10;
const RATE_WINDOW_MS = 60 * 1000;

function isRateLimited(ip) {
  const now = Date.now();
  const entry = requestLog[ip];
  if (!entry) {
    requestLog[ip] = { count: 1, windowStart: now };
    return false;
  }
  if (now - entry.windowStart > RATE_WINDOW_MS) {
    requestLog[ip] = { count: 1, windowStart: now };
    return false;
  }
  entry.count++;
  return entry.count > RATE_LIMIT;
}

function cleanupRequestLog() {
  const now = Date.now();
  for (const ip in requestLog) {
    if (now - requestLog[ip].windowStart > RATE_WINDOW_MS * 5) {
      delete requestLog[ip];
    }
  }
}

async function getCachedRoute(cacheKey) {
  try {
    const store = getStore('planner-routes-cache');
    const cached = await store.get(cacheKey, { type: 'json' });
    return cached || null;
  } catch (e) {
    console.error('Cache read error:', e.message);
    return null;
  }
}

async function saveCachedRoute(cacheKey, route) {
  try {
    const store = getStore('planner-routes-cache');
    await store.setJSON(cacheKey, route);
  } catch (e) {
    console.error('Cache write error:', e.message);
  }
}

exports.handler = async (event) => {
  if (event.httpMethod !== 'POST') {
    return { statusCode: 405, body: 'Method not allowed' };
  }

  const ip = event.headers['x-nf-client-connection-ip']
    || event.headers['client-ip']
    || event.headers['x-forwarded-for']
    || 'unknown';

  cleanupRequestLog();

  if (isRateLimited(ip)) {
    console.warn('Rate limit exceeded for IP:', ip);
    return {
      statusCode: 429,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ error: 'Too many requests, please slow down' })
    };
  }

  const startedAt = Date.now(); // ⚠️ ТЕСТ: засекаем время для диагностики

  try {
    const data = JSON.parse(event.body);

    if (!data || typeof data !== 'object' || !data.category || !data.days) {
      return {
        statusCode: 400,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ error: 'Invalid request data' })
      };
    }

    const season = getSeasonFromDate(data.arrivalDate);
    const lang = (data.lang || 'en').toLowerCase();
    const cacheKey = 'TEST_' + [
      data.category, data.days, data.group,
      data.fitness, data.vibe, data.accommodation,
      data.food_pref, data.budget, data.startCity, season, lang
    ].join('_').replace(/[^a-zA-Z0-9_]/g, '').toLowerCase();

    // ⚠️ ТЕСТ: кэш временно ОТКЛЮЧЕН (закомментирован), чтобы гарантированно
    // проверять именно скорость генерации, а не попадание в кэш
    // const cached = await getCachedRoute(cacheKey);
    // if (cached) {
    //   console.log('Cache hit:', cacheKey);
    //   return {
    //     statusCode: 200,
    //     headers: { 'Content-Type': 'application/json' },
    //     body: JSON.stringify(personalize(cached, data))
    //   };
    // }

    console.log('TEST: Cache miss (disabled), generating:', cacheKey);
    const fileWarnings = getRelevantFileWarnings(data);
    const route = await generateRoute(data, season, lang, fileWarnings);

    const elapsedMs = Date.now() - startedAt;
    console.log(`TEST: Generation took ${elapsedMs}ms`); // ⚠️ ТЕСТ: главная метрика

    await saveCachedRoute(cacheKey, route);

    return {
      statusCode: 200,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(personalize(route, data))
    };

  } catch (e) {
    const elapsedMs = Date.now() - startedAt;
    console.error(`Planner error after ${elapsedMs}ms:`, e);
    return {
      statusCode: 500,
      body: JSON.stringify({ error: 'Failed to generate route', message: e.message })
    };
  }
};

function getSeasonFromDate(dateStr) {
  if (!dateStr) return 'summer';
  const month = new Date(dateStr).getMonth();
  if (month >= 11 || month <= 1) return 'winter';
  if (month >= 2 && month <= 4) return 'spring';
  if (month >= 5 && month <= 7) return 'summer';
  return 'autumn';
}

function getRelevantFileWarnings(data) {
  const w = loadWarnings();
  if (!w) return [];
  const texts = [];
  (w.general || []).forEach(item => texts.push(item.text));
  const cat = (data.category || '').toLowerCase();
  const isMountainTrip = cat.includes('mountain') || cat.includes('nature') || cat.includes('all');
  if (isMountainTrip) {
    (w.road_warnings || []).forEach(item => texts.push(item.text));
  }
  return texts;
}

function personalize(route, data) {
  const personalized = JSON.parse(JSON.stringify(route));
  personalized._personalName = data.name || 'Traveler';
  personalized._personalDate = data.arrivalDate || '';
  return personalized;
}

async function generateRoute(data, season, lang, fileWarnings) {
  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) throw new Error('No API key');

  const LANG_NAMES = {
    ru: 'Russian', en: 'English', tr: 'Turkish', ar: 'Arabic',
    he: 'Hebrew', fa: 'Persian (Farsi)', de: 'German', it: 'Italian', es: 'Spanish'
  };
  const langName = LANG_NAMES[lang] || 'English';

  const fileWarningsBlock = (fileWarnings && fileWarnings.length)
    ? `\n\nTranslate these standard warnings into ${langName} and put them FIRST in "warnings":\n${fileWarnings.map(w => `- ${w}`).join('\n')}`
    : '';

  // ⚠️ ТЕСТ: сильно упрощённый промпт — без блока schedulingFacts (список
  // подробных правил про расписание), без yoga-блока, с явной просьбой
  // писать КОРОТКО, чтобы Claude сгенерировал ответ за секунды, а не за 30+.
  const prompt = `You are a Georgia (Caucasus) travel guide.
Create a BRIEF travel route based on:
- Category: ${data.category}
- Days: ${data.days}
- Group: ${data.group}
- Budget per day: ${data.budget}
- Start city: ${data.startCity}
- Season: ${season}

IMPORTANT: Write ALL text (title, tagline, tips, warnings) in ${langName}.
Keep every tip and warning to 5-8 words maximum. Keep it SHORT overall — this is a quick draft, not a full detailed guide.${fileWarningsBlock}

Return ONLY valid JSON, no markdown:
{
  "title": "route title",
  "tagline": "short one-liner",
  "days": [
    {
      "day": 1,
      "location": "city name",
      "title": "day theme",
      "drive_from_prev": "",
      "schedule": [
        {"time": "10:00", "place": "place name", "duration": "2 hours", "tip": "short tip", "price_mid": "$25"}
      ],
      "food": {"breakfast": "where", "lunch": "where", "dinner": "where"},
      "hotel": {"budget": "name $price", "mid": "name $price", "luxury": "name $price"},
      "shops": ["shop name"],
      "shop_warning": ""
    }
  ],
  "packing_list": {
    "documents": ["item"], "clothes": ["item"], "tech": ["item"],
    "medicine": ["item"], "food_water": ["item"]
  },
  "budget_total": {"budget": "$XXX", "mid": "$XXX", "luxury": "$XXX"},
  "warnings": ["short route-specific warning"],
  "google_maps_url": "https://www.google.com/maps/dir/Point1/Point2/"
}`;

  return new Promise((resolve, reject) => {
    const body = JSON.stringify({
      model: 'claude-haiku-4-5-20251001',
      // ⚠️ ТЕСТ: сильно урезанный max_tokens, чтобы ответ был короче и быстрее.
      // Для реального использования это НЕДОСТАТОЧНО — вернуть обычную формулу
      // после теста!
      max_tokens: Math.min(3000, Math.max(1500, parseInt(data.days || 3) * 300)),
      messages: [{ role: 'user', content: prompt }]
    });

    const options = {
      hostname: 'api.anthropic.com',
      path: '/v1/messages',
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': apiKey,
        'anthropic-version': '2023-06-01',
        'Content-Length': Buffer.byteLength(body)
      }
    };

    const req = https.request(options, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try {
          const result = JSON.parse(data);
          if (!result.content || !result.content[0]) {
            reject(new Error('Unexpected Claude API response: ' + data.slice(0, 200)));
            return;
          }
          const text = result.content[0].text.trim()
            .replace(/^```json\s*/, '').replace(/\s*```$/, '');
          resolve(JSON.parse(text));
        } catch (e) {
          reject(new Error('Failed to parse Claude response: ' + e.message));
        }
      });
    });

    req.on('error', reject);
    req.write(body);
    req.end();
  });
}
