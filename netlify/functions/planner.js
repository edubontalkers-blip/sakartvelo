const https = require('https');
const fs = require('fs');
const path = require('path');
const { getStore } = require('@netlify/blobs');

// ══════════════════════════════════════
// СЕЗОННЫЕ ПРЕДУПРЕЖДЕНИЯ ИЗ warnings.json
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

// ══════════════════════════════════════
// RATE LIMITING
// ══════════════════════════════════════
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

// ══════════════════════════════════════
// ПЕРСИСТЕНТНЫЙ КЭШ (Netlify Blobs)
// ══════════════════════════════════════
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

// ══════════════════════════════════════
// HANDLER
// ══════════════════════════════════════
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
    const cacheKey = [
      data.category, data.days, data.group,
      data.fitness, data.vibe, data.accommodation,
      data.food_pref, data.budget, data.startCity, season, lang
    ].join('_').replace(/[^a-zA-Z0-9_]/g, '').toLowerCase();

    const cached = await getCachedRoute(cacheKey);
    if (cached) {
      console.log('Cache hit:', cacheKey);
      return {
        statusCode: 200,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(personalize(cached, data))
      };
    }

    console.log('Cache miss, generating:', cacheKey);
    const fileWarnings = getRelevantFileWarnings(data);
    const route = await generateRoute(data, season, lang, fileWarnings);

    await saveCachedRoute(cacheKey, route);

    return {
      statusCode: 200,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(personalize(route, data))
    };

  } catch (e) {
    console.error('Planner error:', e);
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
    ? `\n\nThese standard warnings are always true for Georgia right now — translate them into ${langName} exactly as facts (don't alter the meaning) and put them FIRST in the "warnings" array, before any route-specific ones:\n${fileWarnings.map(w => `- ${w}`).join('\n')}`
    : '';

  const yogaBlock = (data.category === 'yoga') ? `

CATEGORY-SPECIFIC GUIDANCE FOR "yoga":
This traveler wants a yoga-focused trip, not a generic sightseeing trip.
Build a route that actually CONNECTS different cities/regions known for yoga
and wellness in Georgia, one leg per multi-day stop rather than a single city:
- Tbilisi: yoga studios in Vake/Vera neighborhoods, morning classes, city-based retreats.
- Batumi / Black Sea coast: beachfront yoga, sunrise sessions by the sea, subtropical botanical garden walks.
- Kazbegi or Svaneti mountains: mountain yoga retreats, quiet nature-based practice, hiking combined with yoga.
- Borjomi: yoga combined with mineral spring relaxation and forest air.
Each day's schedule should include an actual yoga/meditation session as a
schedule item (morning or evening), not just generic sightseeing. Mention
specific neighborhood or area names for studios/retreats where relevant,
and connect the legs with realistic travel times between them.` : '';

  const schedulingFacts = `
SCHEDULING CONSTRAINTS — use these real, stable facts to build a realistic
time-of-day schedule (don't schedule things outside these windows):
- Tbilisi Metro: operates 6:00–24:00 daily.
- Museums (National Museum, most others): typically 10:00–18:00, many closed on Mondays.
- Churches and monasteries (Gergeti Trinity, Jvari, Alaverdi, Bodbe, Svetitskhoveli): generally open dawn to dusk, roughly 9:00–19:00, but may close briefly during services.
- Sulfur baths (Abanotubani, Tbilisi): most private rooms bookable 9:00–23:00.
- Intercity trains (Tbilisi↔Batumi, Tbilisi↔Zugdidi/Svaneti, Tbilisi↔Kutaisi): only 1-3 departures per day, mostly morning or overnight — do not assume trains run every hour; pick one realistic departure time and build the day around it.
- Intercity marshrutkas (shared minivans): more frequent than trains, roughly every 1-2 hours during daytime (approx. 7:00–19:00), but stop running after dark on most rural routes.
- Cable cars (Narikala, Gudauri, Chiatura, Rike Park): typically operate 10:00–22:00 (Tbilisi ones), mountain resort ones daylight hours only.
- Wine cellars / qvevri tastings in Kakheti: typically need advance booking, usually run 11:00–17:00.
- Restaurants: lunch service ~13:00–16:00, dinner ~19:00–23:00; small-town restaurants may close earlier.
- Mountain driving (Georgian Military Highway, Svaneti roads): strongly avoid scheduling after dark — plan arrivals in mountain regions before ~18:00.
When you assign a "time" in the schedule, make sure it is consistent with these
realistic windows and with travel time between locations (don't put someone in
two towns 3 hours apart within the same hour).${yogaBlock}`;

  const prompt = `You are an expert Georgia (country in Caucasus) travel guide.
Create a detailed travel route based on:
- Category: ${data.category}
- Days: ${data.days}
- Group: ${data.group}
- Fitness: ${data.fitness}
- Vibe: ${data.vibe}
- Accommodation: ${data.accommodation}
- Food preference: ${data.food_pref}
- Budget per day: ${data.budget}
- Start city: ${data.startCity}
- Season: ${season}

IMPORTANT: Write ALL text content (title, tagline, day themes, tips, warnings —
everything except place names, which should stay in their common form) in
${langName}. The person using this guide reads ${langName}, not English.${fileWarningsBlock}
${schedulingFacts}

Return ONLY valid JSON, no markdown, no explanation:
{
  "title": "route title",
  "tagline": "inspiring one-liner",
  "days": [
    {
      "day": 1,
      "location": "city name",
      "title": "day theme",
      "drive_from_prev": "drive time (or empty for day 1)",
      "schedule": [
        {
          "time": "10:00",
          "place": "place name",
          "duration": "2 hours",
          "tip": "practical tip",
          "price_mid": "$25"
        }
      ],
      "food": {"breakfast": "where", "lunch": "where", "dinner": "where"},
      "hotel": {"budget": "name $price", "mid": "name $price", "luxury": "name $price"},
      "shops": ["shop name and note"],
      "shop_warning": "warning if no shops ahead"
    }
  ],
  "packing_list": {
    "documents": ["item"],
    "clothes": ["item"],
    "tech": ["item"],
    "medicine": ["item"],
    "food_water": ["item"]
  },
  "budget_total": {"budget": "$XXX", "mid": "$XXX", "luxury": "$XXX"},
  "warnings": ["route-specific warning only — e.g. tied to this exact itinerary or these exact locations. Do NOT include generic Georgia-wide advice like insurance requirements, ATM availability, or phone signal — that is handled separately."],
  "google_maps_url": "https://www.google.com/maps/dir/Point1/Point2/"
}`;

  return new Promise((resolve, reject) => {
    const body = JSON.stringify({
      model: 'claude-haiku-4-5-20251001',
      // Было: Math.max(4000, days*350+1500) — для 7 дней давало ровно 4000,
      // этого не хватало на подробный JSON-маршрут, особенно переведённый
      // на языки с менее эффективной токенизацией (русский, арабский, иврит,
      // фарси используют больше токенов на тот же смысловой объём, чем
      // английский) — ответ обрывался на середине строки (Unterminated string).
      // Подняли базовый порог и множитель на день с запасом.
      max_tokens: Math.min(16000, Math.max(6000, parseInt(data.days || 5) * 700 + 2500)),
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
