// netlify/functions/translate.js
// Прокси для перевода — MyMemory API с умным выбором source language

exports.handler = async function(event) {
  const headers = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Content-Type': 'application/json',
    'Cache-Control': 'public, max-age=86400' // кешируем на 24ч на CDN
  };

  if (event.httpMethod === 'OPTIONS') {
    return { statusCode: 200, headers, body: '' };
  }
  if (event.httpMethod !== 'POST') {
    return { statusCode: 405, headers, body: JSON.stringify({ error: 'Method not allowed' }) };
  }

  let body;
  try { body = JSON.parse(event.body); }
  catch (e) { return { statusCode: 400, headers, body: JSON.stringify({ error: 'Invalid JSON' }) }; }

  const { texts, target, source = 'ru' } = body;

  if (!texts || !Array.isArray(texts) || !target) {
    return { statusCode: 400, headers, body: JSON.stringify({ error: 'Missing texts or target' }) };
  }

  // Нативные языки — возвращаем без перевода
  const nativeLangs = ['ru', 'en', 'de', 'fr', 'tr', 'ar', 'he', 'fa', 'es', 'uk', 'be', 'ka'];
  const shortTarget = target.split('-')[0];
  if (nativeLangs.includes(shortTarget)) {
    return { statusCode: 200, headers, body: JSON.stringify({ translated: texts, native: true }) };
  }

  // Для азиатских языков используем EN как источник (лучшее качество)
  const asianLangs = ['zh', 'ja', 'ko', 'th', 'vi', 'id', 'ms', 'hi', 'bn'];
  const useEnSource = asianLangs.includes(shortTarget);
  const srcLang = useEnSource ? 'en' : source;

  // Параллельный перевод батчами по 5
  const BATCH = 5;
  const translated = new Array(texts.length).fill('');

  for (let i = 0; i < texts.length; i += BATCH) {
    const batch = texts.slice(i, i + BATCH);
    const results = await Promise.all(
      batch.map(async (text) => {
        if (!text || text.trim().length < 2) return text;
        if (/<[a-z]/i.test(text)) return text; // HTML — не трогаем
        if (/^\d+[\d\s₾$€£]*$/.test(text.trim())) return text; // числа — не трогаем
        try {
          const langpair = `${srcLang}|${target}`;
          const url = `https://api.mymemory.translated.net/get?q=${encodeURIComponent(text.trim())}&langpair=${langpair}`;
          const res = await fetch(url, { signal: AbortSignal.timeout(6000) });
          const data = await res.json();
          const tr = data?.responseData?.translatedText;
          // Проверяем качество — если совпадает с оригиналом или слишком короткий
          if (tr && tr.length > 1 && tr !== text && tr !== 'TRANSLATION LIMIT REACHED') {
            return tr;
          }
          return text;
        } catch (e) {
          return text;
        }
      })
    );
    results.forEach((r, idx) => { translated[i + idx] = r; });
  }

  return {
    statusCode: 200,
    headers,
    body: JSON.stringify({ translated, source: srcLang, target })
  };
};
