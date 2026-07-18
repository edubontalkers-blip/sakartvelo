// netlify/functions/generate-pdf.js
//
// Серверная генерация PDF через headless Chrome (Puppeteer).
// В отличие от клиентского jsPDF, настоящий браузер умеет правильно
// "сшивать" арабские и персидские буквы (contextual shaping) — то,
// что jsPDF в принципе не умеет.
//
// ВАЖНО: этот файл написан без возможности протестировать его в реальном
// Netlify-окружении (в песочнице разработки нет сети и Chromium).
// Первый деплой стоит проверить внимательно — возможны небольшие правки
// под конкретную версию Node/Netlify.

const chromium = require('@sparticuz/chromium');
const puppeteer = require('puppeteer-core');

exports.handler = async (event) => {
  if (event.httpMethod !== 'POST') {
    return { statusCode: 405, body: 'Method not allowed' };
  }

  let browser = null;

  try {
    const { route, lang, name, em, pt } = JSON.parse(event.body);

    if (!route || !lang) {
      return {
        statusCode: 400,
        body: JSON.stringify({ error: 'Missing route or lang' })
      };
    }

    const html = buildPdfHtml(route, lang, name || 'Traveler', em, pt);

    browser = await puppeteer.launch({
      args: chromium.args,
      defaultViewport: chromium.defaultViewport,
      executablePath: await chromium.executablePath(),
      headless: chromium.headless,
    });

    const page = await browser.newPage();
    await page.setContent(html, { waitUntil: 'networkidle0' });

    const pdfBuffer = await page.pdf({
      format: 'A4',
      printBackground: true,
      margin: { top: '0mm', bottom: '0mm', left: '0mm', right: '0mm' }
    });

    await browser.close();

    return {
      statusCode: 200,
      headers: {
        'Content-Type': 'application/pdf',
        'Content-Disposition': `attachment; filename="georgia-guide-${(name||'traveler').replace(/\s+/g,'-').toLowerCase()}.pdf"`
      },
      body: pdfBuffer.toString('base64'),
      isBase64Encoded: true
    };

  } catch (e) {
    console.error('generate-pdf error:', e);
    if (browser) { try { await browser.close(); } catch (_) {} }
    return {
      statusCode: 500,
      body: JSON.stringify({ error: 'PDF generation failed', message: e.message })
    };
  }
};

// ══════════════════════════════════════
// HTML-ШАБЛОН ДЛЯ PDF
// ══════════════════════════════════════
// Полноценный HTML с CSS — Chromium рендерит его как обычную страницу,
// поэтому здесь работает всё: RTL, шрифты Google Fonts с правильным
// шейпингом арабского/иврита/фарси, эмодзи, градиенты и т.д.

function esc(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function buildPdfHtml(route, lang, name, em, pt) {
  const isRTL = (lang === 'ar' || lang === 'he' || lang === 'fa');
  const dir = isRTL ? 'rtl' : 'ltr';

  // Google Fonts покрывают латиницу, кириллицу, иврит и арабский/фарси
  // с правильным шейпингом — Chromium сам всё "сошьёт" как надо.
  const fontsLink = `
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Noto+Sans:wght@400;700&family=Noto+Sans+Hebrew:wght@400;700&family=Noto+Sans+Arabic:wght@400;700&display=swap" rel="stylesheet">
  `;

  const fontFamily = isRTL
    ? `'Noto Sans Arabic','Noto Sans Hebrew','Noto Sans',sans-serif`
    : `'Noto Sans',sans-serif`;

  const wine = '#8b1a1a', gold = '#c4960a', dark = '#1a0d10';

  const daysHtml = (route.days || []).map(d => `
    <div class="day-page">
      <div class="day-header">${esc(pt.dayLabel)} ${d.day} — ${esc((d.location||'').toUpperCase())}</div>
      <h2 class="day-title">${esc(d.title)}</h2>
      ${d.drive_from_prev ? `<div class="drive-note">${esc(pt.drive)}${esc(d.drive_from_prev)}</div>` : ''}
      <h3 class="section-title">${esc(pt.schedule)}</h3>
      ${(d.schedule||[]).map(s => `
        <div class="schedule-item">
          <div class="schedule-row">
            <span class="time">${esc(s.time)}</span>
            <span class="place">${esc(s.place)}</span>
            <span class="duration">${esc(s.duration)}</span>
          </div>
          ${s.tip ? `<div class="tip">${esc(pt.tip)}${esc(s.tip)}</div>` : ''}
          ${s.price_mid ? `<div class="price">${esc(s.price_mid)}</div>` : ''}
        </div>
      `).join('')}
      <h3 class="section-title">${esc(pt.food)}</h3>
      <div class="food-block">
        ${d.food && d.food.breakfast ? `<div><b>${esc(pt.morning)}</b>${esc(d.food.breakfast)}</div>` : ''}
        ${d.food && d.food.lunch ? `<div><b>${esc(pt.lunch)}</b>${esc(d.food.lunch)}</div>` : ''}
        ${d.food && d.food.dinner ? `<div><b>${esc(pt.dinner)}</b>${esc(d.food.dinner)}</div>` : ''}
      </div>
      <h3 class="section-title">${esc(pt.accommodation)}</h3>
      <div class="hotel-block">
        ${d.hotel && d.hotel.budget ? `<div class="hotel-budget">${esc(d.hotel.budget)}</div>` : ''}
        ${d.hotel && d.hotel.mid ? `<div class="hotel-mid">${esc(d.hotel.mid)}</div>` : ''}
        ${d.hotel && d.hotel.luxury ? `<div class="hotel-luxury">${esc(d.hotel.luxury)}</div>` : ''}
      </div>
      ${d.shops && d.shops.length ? `
        <h3 class="section-title">${esc(pt.shopsOnRoute)}</h3>
        ${d.shops.map(sh => `<div class="shop-item">+ ${esc(sh)}</div>`).join('')}
        ${d.shop_warning ? `<div class="shop-warning">${esc(d.shop_warning)}</div>` : ''}
      ` : ''}
    </div>
  `).join('');

  const packingSections = [
    { title: pt.documents, items: route.packing_list?.documents },
    { title: pt.clothes, items: route.packing_list?.clothes },
    { title: pt.tech, items: route.packing_list?.tech },
    { title: pt.medicine, items: route.packing_list?.medicine },
    { title: pt.foodWater, items: route.packing_list?.food_water },
  ];

  const packingHtml = packingSections.map(sec => `
    <h3 class="section-title">${esc(sec.title)}</h3>
    ${(sec.items||[]).map(item => `<div class="pack-item">☐ ${esc(item)}</div>`).join('')}
  `).join('');

  const warningsHtml = (route.warnings || []).map((w, i) => `
    <div class="warning-box ${i === 0 ? 'warning-red' : 'warning-yellow'}">
      ${i === 0 ? '!!! ' : '!! '}${esc(w)}
    </div>
  `).join('');

  const embassiesHtml = AFFILIATE_EMBASSIES.map(e => `
    <div class="row"><span>${esc(e.country)}</span><span>${esc(e.phone)}</span></div>
  `).join('');

  const affiliateHtml = `
    <div class="affiliate-block">
      <a href="${AFFILIATE_LINKS.hotel}">🏨 ${isRTL ? '' : 'Book hotels'}</a>
      <a href="${AFFILIATE_LINKS.transfer}">🚕 ${isRTL ? '' : 'Book a transfer'}</a>
      <a href="${AFFILIATE_LINKS.tour}">🗺️ ${isRTL ? '' : 'Book tours'}</a>
      <a href="${AFFILIATE_LINKS.esim}">📲 ${isRTL ? '' : 'Get an eSIM'}</a>
      <a href="${AFFILIATE_LINKS.flights}">✈️ ${isRTL ? '' : 'Find flights'}</a>
      <a href="${AFFILIATE_LINKS.insurance}">🛡️ ${isRTL ? '' : 'Insurance (Aldagi)'}</a>
    </div>
  `;

  return `<!DOCTYPE html>
<html lang="${lang}" dir="${dir}">
<head>
<meta charset="UTF-8">
${fontsLink}
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: ${fontFamily}; color: ${dark}; }
  .page { width: 210mm; min-height: 297mm; padding: 18mm; page-break-after: always; position: relative; }
  .cover { background: ${dark}; color: #fff; text-align: center; padding-top: 60mm; }
  .cover .brand { color: ${gold}; font-size: 14pt; margin-bottom: 20mm; }
  .cover .flag { font-size: 40pt; margin-bottom: 10mm; }
  .cover .title { font-size: 26pt; font-weight: 700; margin-bottom: 6mm; }
  .cover .tagline { color: ${gold}; font-size: 13pt; margin-bottom: 20mm; }
  .cover .name { font-size: 18pt; font-weight: 700; margin-bottom: 4mm; }
  .cover .meta { font-size: 11pt; color: #ccb8a8; }
  .section-header { background: ${wine}; color: #fff; padding: 6mm 8mm; font-size: 13pt; font-weight: 700; }
  .day-header { color: ${wine}; font-size: 9pt; font-weight: 700; margin-top: 4mm; }
  .day-title { color: ${wine}; font-size: 14pt; margin-bottom: 4mm; }
  .section-title { font-size: 11pt; font-weight: 700; margin: 5mm 0 2mm; }
  .schedule-item { background: #f9f5f0; border-radius: 3mm; padding: 3mm 4mm; margin-bottom: 2mm; }
  .schedule-row { display: flex; justify-content: space-between; gap: 3mm; font-size: 10pt; }
  .time { color: ${gold}; font-weight: 700; }
  .place { font-weight: 700; flex: 1; }
  .tip, .price { font-size: 9pt; color: #555; margin-top: 1mm; }
  .price { color: ${wine}; }
  .drive-note { background: #f0f8f0; border-radius: 2mm; padding: 2mm 3mm; font-size: 9pt; color: #286428; margin-bottom: 3mm; }
  .hotel-budget { color: #287828; } .hotel-mid { color: #966400; } .hotel-luxury { color: ${wine}; }
  .shop-item { color: #287828; font-size: 9pt; }
  .shop-warning { background: #fff0e6; color: #b43c00; font-weight: 700; padding: 2mm 3mm; border-radius: 2mm; margin-top: 2mm; font-size: 9pt; }
  .pack-item { font-size: 10pt; margin-bottom: 1mm; }
  .warning-box { border-radius: 3mm; padding: 3mm 4mm; margin-bottom: 3mm; font-size: 10pt; font-weight: 700; }
  .warning-red { background: #ffe6e6; color: #8b0000; }
  .warning-yellow { background: #fff8dc; color: #856404; }
  .emergency-page { background: ${wine}; color: #fff; }
  .emergency-page .big-number { background: ${gold}; color: ${dark}; text-align: center; border-radius: 4mm; padding: 6mm; font-size: 24pt; font-weight: 700; margin: 6mm 0; }
  .row { display: flex; justify-content: space-between; font-size: 10pt; padding: 1.5mm 0; }
  .affiliate-block { display: flex; flex-wrap: wrap; gap: 3mm; margin-top: 5mm; }
  .affiliate-block a { color: ${wine}; text-decoration: none; font-size: 9pt; border: 1px solid ${gold}; border-radius: 20mm; padding: 2mm 4mm; }
</style>
</head>
<body>

  <div class="page cover">
    <div class="brand">🍶 sakartvelo.ai</div>
    <div class="flag">🇬🇪</div>
    <div class="title">${esc(route.title)}</div>
    <div class="tagline">${esc(route.tagline)}</div>
    <div class="name">${esc(name)}</div>
    <div class="meta">${(route.days||[]).length} · ${new Date().toLocaleDateString()}</div>
  </div>

  <div class="page">
    <div class="section-header">${esc(pt.routeOverview)}</div>
    ${(route.days||[]).map(d => `<div class="row"><span>${esc(pt.dayLabel)} ${d.day}</span><span>${esc(d.location)} — ${esc(d.title)}</span></div>`).join('')}
    <h3 class="section-title">${esc(pt.budgetEstimate)}</h3>
    <div class="row"><span>${esc(pt.economy)}</span><span>${esc(route.budget_total?.budget || '')}</span></div>
    <div class="row"><span>${esc(pt.midRange)}</span><span>${esc(route.budget_total?.mid || '')}</span></div>
    <div class="row"><span>${esc(pt.luxury)}</span><span>${esc(route.budget_total?.luxury || '')}</span></div>
    ${affiliateHtml}
  </div>

  <div class="page">${daysHtml}</div>

  <div class="page">
    <div class="section-header">${esc(pt.packingList)}</div>
    ${packingHtml}
  </div>

  <div class="page">
    <div class="section-header">${esc(pt.warnings)}</div>
    ${warningsHtml}
  </div>

  <div class="page emergency-page">
    <div class="section-header" style="background:transparent;text-align:center">${esc(em.title)}</div>
    <div class="big-number">112</div>
    <div class="row"><span>${esc(em.amb)}</span><span>112</span></div>
    <div class="row"><span>${esc(em.pol)}</span><span>112</span></div>
    <div class="row"><span>${esc(em.fire)}</span><span>111</span></div>
    <div class="row"><span>${esc(em.priv)}</span><span>+995 32 244 44 44</span></div>
    <div class="row"><span>${esc(em.tour)}</span><span>1505</span></div>
    <h3 class="section-title" style="color:${gold}">${esc(em.embTitle)}</h3>
    ${embassiesHtml}
    <h3 class="section-title" style="color:${gold}">${esc(em.ins)}</h3>
    <div class="row"><span>Aldagi</span><span>+995 32 244 44 00</span></div>
  </div>

</body>
</html>`;
}

// Настоящие партнёрские ссылки (взяты с главной страницы sakartvelo.ai)
const AFFILIATE_LINKS = {
  hotel: 'https://www.booking.com/searchresults.html?aid=7916610',
  transfer: 'https://kiwitaxi.com/?marker=732753',
  tour: 'https://www.viator.com/searchResults/all?text=Georgia',
  insurance: 'https://travelgeorgia.aldagi.ge/',
  esim: 'https://airalo.tpm.lv/lbmgE1xZ',
  flights: 'https://aviasales.tpm.lv/3v2DPRiC'
};

const AFFILIATE_EMBASSIES = [
  { country: 'Russia (Interests Section, via Swiss Embassy) / Россия', phone: '+995 32 291 24 53' },
  { country: 'United Kingdom', phone: '+995 32 227 47 47' },
  { country: 'Germany / Deutschland', phone: '+995 32 244 73 00' },
  { country: 'Israel / ישראל', phone: '+995 32 255 65 00' },
  { country: 'Iran / ایران', phone: '+995 32 291 36 57' },
  { country: 'Turkey / Türkiye', phone: '+995 32 225 20 72' },
  { country: 'Italy / Italia', phone: '+995 32 299 64 18' },
  { country: 'Spain / España', phone: '+995 32 220 00 63' },
  { country: 'Saudi Arabia', phone: '+995 32 200 95 04' },
];
