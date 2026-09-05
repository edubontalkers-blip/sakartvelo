const https = require('https');
const fs = require('fs');
const path = require('path');
const { getStore } = require('@netlify/blobs');

// ══════════════════════════════════════
// ПОДКЛЮЧЕНИЕ К NETLIFY BLOBS С ЯВНЫМИ ДАННЫМИ
// ══════════════════════════════════════
// На этом сайте автоматическая настройка Netlify Blobs (zero-config) не
// срабатывает — getStore() без параметров падал с MissingBlobsEnvironmentError.
// Обходной путь: явно передаём siteID и personal access token через
// переменные окружения BLOBS_SITE_ID и BLOBS_TOKEN (заданы в Netlify UI).
function getStoreExplicit(name) {
  return getStore({
    name,
    siteID: process.env.BLOBS_SITE_ID,
    token: process.env.BLOBS_TOKEN
  });
}

// ══════════════════════════════════════
// ФОНОВАЯ ФУНКЦИЯ (Netlify Background Function)
// ══════════════════════════════════════
// Файл называется "planner-background.js" — суффикс "-background" в имени
// файла говорит Netlify автоматически считать эту функцию фоновой.
// Отличие от обычной planner.js:
// - Netlify сразу отвечает вызывающей стороне (202 Accepted), не дожидаясь
//   результата — поэтому результат нельзя вернуть напрямую в ответе.
// - Зато у функции есть до 15 МИНУТ на выполнение вместо 10/26 секунд.
// - Результат сохраняется в Netlify Blobs под уникальным jobId, который
//   передаёт клиент. Сайт (planner/index.html) потом отдельно спрашивает
//   через planner-status.js: "готово?" — это называется "поллинг" (polling).
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
// RATE LIMITING (та же логика, что и раньше)
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
// КЭШ ГОТОВЫХ МАРШРУТОВ (как раньше — экономит токены)
// ══════════════════════════════════════
async function getCachedRoute(cacheKey) {
  try {
    const store = getStoreExplicit('planner-routes-cache');
    const cached = await store.get(cacheKey, { type: 'json' });
    return cached || null;
  } catch (e) {
    console.error('Route cache read error:', e.message);
    return null;
  }
}

async function saveCachedRoute(cacheKey, route) {
  try {
    const store = getStoreExplicit('planner-routes-cache');
    await store.setJSON(cacheKey, route);
  } catch (e) {
    console.error('Route cache write error:', e.message);
  }
}

// ══════════════════════════════════════
// ХРАНИЛИЩЕ СТАТУСОВ ЗАДАЧ (для поллинга с сайта)
// ══════════════════════════════════════
async function setJobStatus(jobId, statusObj) {
  try {
    const store = getStoreExplicit('planner-jobs');
    await store.setJSON(jobId, statusObj);
  } catch (e) {
    console.error('Job status write error:', e.message);
  }
}

// ══════════════════════════════════════
// ДНЕВНОЙ ЛИМИТ: 1 бесплатный маршрут в день на IP-адрес
// ══════════════════════════════════════
function todayDateStr() {
  return new Date().toISOString().slice(0, 10); // YYYY-MM-DD (UTC)
}

async function hasUsedDailyLimit(ip) {
  try {
    const store = getStoreExplicit('planner-daily-limit');
    const key = ip + '_' + todayDateStr();
    const existing = await store.get(key, { type: 'json' });
    return !!existing;
  } catch (e) {
    console.error('Daily limit check error:', e.message);
    return false; // при сбое хранилища — не блокируем пользователя
  }
}

async function markDailyLimitUsed(ip) {
  try {
    const store = getStoreExplicit('planner-daily-limit');
    const key = ip + '_' + todayDateStr();
    await store.setJSON(key, { usedAt: new Date().toISOString() });
  } catch (e) {
    console.error('Daily limit write error:', e.message);
  }
}

// ══════════════════════════════════════
// ГРАНИЦЫ ЧИСЛА ДНЕЙ (защита на бэкенде)
// ══════════════════════════════════════
// Фронтенд теперь предлагает только 1-3 дня, но это не единственная
// точка входа (может быть старая ссылка, кэш, ручной запрос) — здесь
// подстраховка на сервере, чтобы никогда не строить нереалистично
// длинный (и неточный по ценам) маршрут.
const MIN_DAYS = 1;
const MAX_DAYS = 3;

function clampDays(rawDays) {
  const n = parseInt(rawDays, 10);
  if (!Number.isFinite(n) || n < MIN_DAYS) return MIN_DAYS;
  if (n > MAX_DAYS) return MAX_DAYS;
  return n;
}

// ══════════════════════════════════════
// HANDLER (фоновый — Netlify не ждёт return, но мы всё равно
// его делаем для чистоты и для логов)
// ══════════════════════════════════════
exports.handler = async (event) => {
  if (event.httpMethod !== 'POST') {
    return { statusCode: 405, body: 'Method not allowed' };
  }

  let data;
  try {
    data = JSON.parse(event.body);
  } catch (e) {
    console.error('Invalid JSON body:', e.message);
    return { statusCode: 400, body: 'Invalid JSON' };
  }

  const jobId = data.jobId;
  if (!jobId) {
    console.error('Missing jobId in request');
    return { statusCode: 400, body: 'Missing jobId' };
  }

  if (!data.category || !data.days) {
    await setJobStatus(jobId, { status: 'error', message: 'Invalid request data' });
    return { statusCode: 200 };
  }

  // Подстраховка: даже если клиент прислал что-то другое — маршрут
  // строим только на 1-3 дня.
  data.days = clampDays(data.days);

  const ip = event.headers['x-nf-client-connection-ip']
    || event.headers['client-ip']
    || event.headers['x-forwarded-for']
    || 'unknown';

  cleanupRequestLog();

  if (isRateLimited(ip)) {
    console.warn('Rate limit exceeded for IP:', ip);
    await setJobStatus(jobId, { status: 'error', message: 'Too many requests, please slow down' });
    return { statusCode: 200 };
  }

  const isAdminBypass = !!process.env.ADMIN_BYPASS_CODE
    && data.bypassCode === process.env.ADMIN_BYPASS_CODE;

  if (!isAdminBypass && await hasUsedDailyLimit(ip)) {
    console.warn('Daily limit reached for IP:', ip);
    await setJobStatus(jobId, { status: 'limited', message: 'Daily free route limit reached' });
    return { statusCode: 200 };
  }

  await setJobStatus(jobId, { status: 'pending' });

  try {
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
      if (!isAdminBypass) await markDailyLimitUsed(ip);
      await setJobStatus(jobId, { status: 'done', route: personalize(cached, data) });
      return { statusCode: 200 };
    }

    console.log('Cache miss, generating:', cacheKey, 'jobId:', jobId);
    const fileWarnings = getRelevantFileWarnings(data);
    const startedAt = Date.now();
    const route = await generateRoute(data, season, lang, fileWarnings);
    console.log(`Generation finished in ${Date.now() - startedAt}ms for jobId ${jobId}`);

    await saveCachedRoute(cacheKey, route);
    if (!isAdminBypass) await markDailyLimitUsed(ip);
    await setJobStatus(jobId, { status: 'done', route: personalize(route, data) });

    return { statusCode: 200 };

  } catch (e) {
    console.error('Planner background error for jobId', jobId, ':', e);
    await setJobStatus(jobId, { status: 'error', message: e.message });
    return { statusCode: 200 };
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

// ══════════════════════════════════════
// ОЧИСТКА ТЕКСТА ОТ ПРОБЛЕМНЫХ СИМВОЛОВ
// ══════════════════════════════════════
function sanitizeText(str) {
  if (typeof str !== 'string') return str;
  return str
    .replace(/\uFFFD/g, '')           // символ замены (обычно источник "?")
    .replace(/[\u00AD\u2011]/g, '-')  // мягкий и неразрывный дефис → обычный
    .replace(/[\u200B\u200C\u200D\uFEFF]/g, ''); // невидимые zero-width символы
}

function sanitizeDeep(value) {
  if (typeof value === 'string') return sanitizeText(value);
  if (Array.isArray(value)) return value.map(sanitizeDeep);
  if (value && typeof value === 'object') {
    const out = {};
    for (const key in value) out[key] = sanitizeDeep(value[key]);
    return out;
  }
  return value;
}

function personalize(route, data) {
  const personalized = sanitizeDeep(JSON.parse(JSON.stringify(route)));
  personalized._personalName = data.name || 'Traveler';
  personalized._personalDate = data.arrivalDate || '';
  return personalized;
}

// ══════════════════════════════════════
// ОРИЕНТИРЫ ЦЕН (2026) — вместо того чтобы модель гадала цены по памяти
// ══════════════════════════════════════
// ВАЖНО: это приблизительные, ориентировочные цифры, а не гарантированно
// свежие данные — цены в реальности меняются (сезон, инфляция, курс лари).
// Но это ГОРАЗДО честнее, чем полностью выдуманное число без всякой
// привязки к реальности (как было раньше). Модель получает явный диапазон
// и явную инструкцию — писать диапазон, а не точную цифру, и не завышать
// уверенность там, где её не может быть.
//
// TODO (владелец сайта): если на сайте уже есть раздел "Честные цены 2026"
// с проверенными цифрами — эти значения стоит заменить на те же самые,
// чтобы на сайте и в PDF-маршруте не было расхождений.
const PRICE_ANCHORS_2026 = `
APPROXIMATE 2026 PRICE ANCHORS — use these as a realistic baseline for
"price_mid" values. These are ballpark reference ranges, not guaranteed
current prices (prices genuinely change with season/inflation). Because of
that:
- Prefer a RANGE over a single exact number where realistic (e.g. "$8-12"
  rather than a suspiciously precise "$9.37").
- Never invent a price with more confidence than these anchors justify.
- Museum/attraction entrance fees: $2-8 for most sites, $8-15 for major
  national museums.
- City taxi ride (Bolt/Yandex, within Tbilisi/Batumi/Kutaisi): $2-6 for a
  typical in-city trip.
- Intercity transfer/taxi (e.g. Tbilisi–Kazbegi, Tbilisi–Kakheti day trip):
  $40-80 for a full car for the whole group, one-way.
- Tbilisi Metro: fixed 1 GEL (~$0.35) per ride regardless of distance,
  paid with a rechargeable Metromoney card.
- City bus (Tbilisi/Batumi): fixed 0.5-1 GEL (~$0.20-0.35) per ride.
- Intercity marshrutka (shared minivan), e.g. Tbilisi–Kutaisi, Tbilisi–
  Kazbegi, Tbilisi–Batumi: roughly $5-12 per person one-way depending on
  distance — mention this explicitly when a route includes intercity
  travel, so the traveler knows the honest fixed fare and isn't
  overcharged by an individual driver quoting a much higher "tourist price".
- Intercity train (e.g. Tbilisi–Batumi, Tbilisi–Kutaisi/Zugdidi): roughly
  $5-15 per person one-way depending on class.
- Sulfur bath private room (Tbilisi, per hour): $15-40 depending on room
  quality.
- Cable car / ropeway ticket: $3-10.
- Wine tasting with light snacks (Kakheti): $10-25 per person.
- Local restaurant meal, mid-range, per person: $8-18.
- Guesthouse/budget hotel per night: $20-40.
- Mid-range hotel per night: $60-110.
- Museum-quality/boutique hotel per night: $120-250.
When unsure of a specific attraction's exact fee, use the closest category
above rather than fabricating an unrelated number.

IMPORTANT — protect the traveler from overpaying: whenever the schedule
involves intercity travel (marshrutka, bus, train, or hired transfer car
between cities), the "tip" field for that schedule item MUST mention the
honest fixed price range from the anchors above (e.g. "Marshrutka fare is
fixed at about $6-8 per person — don't pay more if a driver quotes higher").
This is specifically to prevent tourists from being overcharged locally.`;

// ══════════════════════════════════════
// ПОГОДА ДЛЯ МАРШРУТА
// ══════════════════════════════════════
// Честное правило: реальный прогноз погоды существует физически только на
// ближайшие ~5 дней вперёд (ограничение любого прогноза, не только нашего).
// Если дата поездки в этих пределах — берём настоящий прогноз напрямую у
// OpenWeather (тот же API, что использует weather.js). Если дата дальше —
// НЕ притворяемся, что знаем погоду точно, а даём спокойные типичные для
// сезона цифры — с явной пометкой, что это ориентир, а не прогноз.

// Типичные (климатические) диапазоны температур по сезону — раздельно для
// низменных городов (Тбилиси/Батуми/Кутаиси) и гор (Казбеги/Местиа/Гудаури),
// потому что разница в горах гораздо заметнее.
const SEASONAL_TYPICAL = {
  winter: { lowland: '2-9°C, occasional rain or light snow', mountain: '-8 to 0°C, snow likely — mountain roads may need chains' },
  spring: { lowland: '10-20°C, changeable, pack a light rain jacket', mountain: '0-12°C, still cold at altitude, some roads may reopen late spring' },
  summer: { lowland: '24-32°C, hot and mostly dry', mountain: '12-22°C, pleasant but bring a warm layer for evenings' },
  autumn: { lowland: '12-22°C, mild, some rain', mountain: '2-12°C, cools quickly, pack layers' },
};

function isMountainCity(cityName) {
  return ['Kazbegi', 'Mestia', 'Gudauri'].indexOf(cityName) !== -1;
}

async function getWeatherContext(data, season) {
  // startCity приходит с эмодзи с фронтенда ("Tbilisi ✈️") — берём чистое имя.
  const cityName = (data.startCity || 'Tbilisi').split(' ')[0];
  const isMountain = isMountainCity(cityName);
  const typical = SEASONAL_TYPICAL[season] || SEASONAL_TYPICAL.summer;
  const typicalRange = isMountain ? typical.mountain : typical.lowland;

  const daysUntilArrival = data.arrivalDate
    ? Math.floor((new Date(data.arrivalDate) - new Date()) / 86400000)
    : null;

  // Дата в пределах ближайших 5 дней — пробуем настоящий прогноз.
  if (daysUntilArrival !== null && daysUntilArrival >= 0 && daysUntilArrival <= 5 && process.env.OPENWEATHER_KEY) {
    try {
      const CITY_COORDS = {
        Tbilisi: { lat: 41.7151, lon: 44.8271 },
        Batumi: { lat: 41.6168, lon: 41.6367 },
        Kutaisi: { lat: 42.2679, lon: 42.6946 },
      };
      const coords = CITY_COORDS[cityName] || CITY_COORDS.Tbilisi;
      const url = `https://api.openweathermap.org/data/2.5/forecast?lat=${coords.lat}&lon=${coords.lon}&appid=${process.env.OPENWEATHER_KEY}&units=metric&cnt=40`;
      const res = await fetch(url);
      const json = await res.json();
      if (json.cod === '200' && json.list && json.list.length) {
        const targetDay = data.arrivalDate;
        const match = json.list.find(item => {
          const d = new Date(item.dt * 1000);
          return d.toISOString().split('T')[0] === targetDay
            && d.getUTCHours() >= 11 && d.getUTCHours() <= 14;
        });
        if (match) {
          return `\nWEATHER — REAL FORECAST (use this exact info, calm and factual tone,
no exaggeration): On ${targetDay} in ${cityName}, expect around
${Math.round(match.main.temp)}°C (${match.weather[0].description}),
wind ${Math.round(match.wind.speed)} m/s, humidity ${match.main.humidity}%.
Mention this briefly and practically (e.g. what to wear/bring), don't dramatize it.`;
        }
      }
    } catch (e) {
      console.warn('Weather fetch failed, falling back to seasonal typical:', e.message);
    }
  }

  // Дата далеко (или прогноз не удался) — честный сезонный ориентир,
  // явно НЕ выдаём за прогноз.
  return `\nWEATHER — SEASONAL TYPICAL (NOT a forecast — the trip date is too far
ahead for an actual forecast to exist, so be upfront about that): around
${cityName} in ${season}, typical weather is ${typicalRange}. Mention this
as a general seasonal expectation ("typically..."), not as a specific
prediction for that exact day. Calm, practical tone — suggest what to pack,
don't create alarm.`;
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
two towns 3 hours apart within the same hour).${yogaBlock}
${PRICE_ANCHORS_2026}`;

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
${await getWeatherContext(data, season)}

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
          "price_mid": "$20-25"
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

  // ⚠️ Фоновая функция может работать до 15 минут — риска таймаута больше нет.
  // Дни теперь всегда 1-3 (clampDays), поэтому запас токенов можно смело
  // держать поменьше — маршрут физически короче.
  const isComplexCategory = (data.category === 'yoga');
  const categoryBuffer = isComplexCategory ? 3000 : 0;
  const computedMaxTokens = Math.max(6000, parseInt(data.days || 3) * 900 + 3000 + categoryBuffer);

  return new Promise((resolve, reject) => {
    const body = JSON.stringify({
      model: 'claude-haiku-4-5-20251001',
      max_tokens: Math.min(64000, computedMaxTokens),
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
