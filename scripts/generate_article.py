#!/usr/bin/env python3
"""
sakartvelo.ai — Daily News/Article Generator
Runs every day via GitHub Actions (.github/workflows/daily-article.yml).

Что делает:
1. Решает тему дня — по чётным дням года "реальные новости" (с веб-поиском
   через Anthropic API), по нечётным — "вечнозелёная" тема из готового пула
   (природа/кухня/история), которая ещё не публиковалась.
2. Просит Claude написать статью сразу на 9 языках (ru, en, tr, ar, he,
   fa, de, it, es) — тот же набор языков, что и везде на сайте.
3. Собирает полноценную самостоятельную HTML-страницу (свой дизайн,
   тёмная тема, переключатель языков) — в News/<slug>/index.html
4. Обновляет news/manifest.json (список всех статей) и пересобирает
   news/index.html — страницу со списком всех новостей.
"""

import json
import os
import random
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
NEWS_DIR = BASE_DIR / 'news'
MANIFEST_FILE = NEWS_DIR / 'manifest.json'

LANGS = ['ru', 'en', 'tr', 'ar', 'he', 'fa', 'de', 'it', 'es']
LANG_NAMES = {
    'ru': 'Russian', 'en': 'English', 'tr': 'Turkish', 'ar': 'Arabic',
    'he': 'Hebrew', 'fa': 'Persian (Farsi)', 'de': 'German', 'it': 'Italian', 'es': 'Spanish'
}

# Небольшой набор цветовых палитр — чтобы страницы визуально отличались
# друг от друга, а не были все одного цвета. Скрипт выбирает одну по хэшу
# темы (детерминированно, без хранения доп. состояния).
PALETTES = [
    {"name": "turquoise", "accent": "#0e7c78", "accent_deep": "#0a5a57", "accent_light": "#4fd8cf"},
    {"name": "forest",    "accent": "#2d6a4f", "accent_deep": "#1b4332", "accent_light": "#74c69d"},
    {"name": "amber",     "accent": "#b45309", "accent_deep": "#7c3a05", "accent_light": "#f5b25e"},
    {"name": "plum",      "accent": "#7c3a6d", "accent_deep": "#54244a", "accent_light": "#c98cb9"},
    {"name": "slate",     "accent": "#3d5a73", "accent_deep": "#25384a", "accent_light": "#8fb3cc"},
]

# Вечнозелёные темы — пул тем без привязки к текущим событиям.
# Скрипт выберет ту, что ещё не публиковалась (нет в manifest.json).
EVERGREEN_TOPICS = [
    "Caucasian leopard — Georgia's rarest big cat",
    "West Caucasian tur — the mountain goat of the Caucasus",
    "Prometheus Cave — underground rivers and stalactites",
    "Colchis rainforests — Georgia's temperate jungle",
    "Javakheti lake plateau — flamingos in the Caucasus",
    "Vashlovani Nature Reserve — Georgia's semi-desert",
    "Georgian qvevri winemaking — 8000 years of tradition",
    "Khinkali etiquette — how Georgians really eat dumplings",
    "Khachapuri regional varieties across Georgia",
    "Georgian supra — the philosophy of the feast",
    "Chacha — Georgia's grape brandy explained",
    "Adjika — the fiery spice paste of Georgia",
    "Queen Tamar — Georgia's golden age ruler",
    "Georgian alphabet — one of 14 unique scripts in the world",
    "Svan towers — medieval defense architecture",
    "Davit Gareja — cave monastery on the Azerbaijan border",
    "Gelati Monastery — UNESCO academy of medieval Georgia",
    "Ananuri Fortress — castle on the Georgian Military Highway",
    "Uplistsikhe — ancient rock-hewn city",
    "Georgian polyphonic singing — UNESCO intangible heritage",
    "The Golden Fleece myth and the real Colchis",
    "Rtveli — the grape harvest festival of Kakheti",
    "Georgian Orthodox Christmas traditions",
    "Tbilisi sulfur baths — history and how to visit",
    "Georgian Military Highway — the road through the Caucasus",
    "Vardzia cave city and Queen Tamar's legacy",
    "Sighnaghi — the city of love and its wall",
    "Georgian dance — Kartuli and the mountain warrior dances",
    "Georgian felt hats and Svan wool crafts",
    "Borjomi mineral water — geology and history",
]


# ══════════════════════════════════════
# JSON-СХЕМА ДЛЯ TOOL USE
# ══════════════════════════════════════
# Вместо того чтобы просить модель вручную напечатать JSON текстом (что
# ненадёжно — легко забыть экранировать кавычку или спецсимвол в тексте на
# 9 языках, из-за чего JSON ломается), используем механизм "tool use" —
# Anthropic API сам гарантирует, что аргументы вызова инструмента будут
# валидным JSON, соответствующим схеме. Это устраняет целый класс ошибок.
ARTICLE_LANG_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "tagline": {"type": "string"},
        "intro": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "body": {"type": "string"}
                },
                "required": ["heading", "body"]
            },
            "minItems": 3,
            "maxItems": 5
        },
        "closing": {"type": "string"}
    },
    "required": ["title", "tagline", "intro", "sections", "closing"]
}

SUBMIT_ARTICLE_TOOL = {
    "name": "submit_article",
    "description": "Submit the finished multi-language article for publishing.",
    "input_schema": {
        "type": "object",
        "properties": {
            "slug": {"type": "string", "description": "Short URL-safe English slug, no spaces"},
            "topic_key": {"type": "string", "description": "Short stable English key for de-duplication"},
            "emoji": {"type": "string", "description": "Single emoji representing the topic"},
            "content": {
                "type": "object",
                "properties": {lang: ARTICLE_LANG_SCHEMA for lang in LANGS},
                "required": LANGS
            }
        },
        "required": ["slug", "topic_key", "emoji", "content"]
    }
}


# ══════════════════════════════════════
# ANTHROPIC API (raw HTTPS, без сторонних библиотек)
# ══════════════════════════════════════
def call_claude(prompt, use_web_search=False, max_tokens=8000):
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        raise RuntimeError('ANTHROPIC_API_KEY is not set')

    tools = [SUBMIT_ARTICLE_TOOL]
    if use_web_search:
        tools.append({"type": "web_search_20250305", "name": "web_search", "max_uses": 5})

    body = {
        "model": "claude-sonnet-4-6",
        "max_tokens": max_tokens,
        "stream": True,
        "tools": tools,
        # Не форсируем tool_choice — модели нужно свободно пользоваться
        # web_search сначала, и только потом вызвать submit_article.
        "messages": [{"role": "user", "content": prompt}]
    }

    data = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(
        'https://api.anthropic.com/v1/messages',
        data=data,
        headers={
            'Content-Type': 'application/json',
            'x-api-key': api_key,
            'anthropic-version': '2023-06-01'
        },
        method='POST'
    )

    # ══════════════════════════════════════
    # ПОТОКОВЫЙ РЕЖИМ (SSE streaming)
    # ══════════════════════════════════════
    blocks = {}  # index -> {"type": ..., "text": "", "name": ...}
    stop_reason = None

    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            for raw_line in resp:
                line = raw_line.decode('utf-8', errors='replace').strip()
                if not line or not line.startswith('data:'):
                    continue
                payload = line[len('data:'):].strip()
                if payload == '[DONE]':
                    break
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    continue

                etype = event.get('type')
                if etype == 'content_block_start':
                    idx = event['index']
                    cb = event['content_block']
                    blocks[idx] = {"type": cb.get('type'), "text": "", "name": cb.get('name')}
                elif etype == 'content_block_delta':
                    idx = event['index']
                    delta = event.get('delta', {})
                    dtype = delta.get('type')
                    if dtype == 'text_delta' and idx in blocks:
                        blocks[idx]['text'] += delta.get('text', '')
                    elif dtype == 'input_json_delta' and idx in blocks:
                        # Частичный JSON аргументов вызова инструмента —
                        # накапливаем как строку, разберём после завершения
                        blocks[idx]['text'] += delta.get('partial_json', '')
                elif etype == 'message_delta':
                    stop_reason = event.get('delta', {}).get('stop_reason', stop_reason)
                elif etype == 'error':
                    raise RuntimeError(f"Anthropic stream error: {event.get('error')}")
    except urllib.error.HTTPError as e:
        err_body = e.read().decode('utf-8', errors='replace')
        raise RuntimeError(f'Anthropic API HTTP {e.code}: {err_body[:1000]}')

    block_types = [(b['type'], b.get('name')) for _, b in sorted(blocks.items())]
    print(f'API response (stream): stop_reason={stop_reason}, blocks={block_types}')

    # Ищем именно вызов инструмента submit_article — его "input" гарантированно
    # валидный JSON благодаря структурированной схеме на стороне API.
    for _, b in sorted(blocks.items()):
        if b['type'] == 'tool_use' and b.get('name') == 'submit_article':
            return json.loads(b['text'])

    raise RuntimeError('submit_article tool was not called. Blocks: ' + json.dumps(block_types))


# ══════════════════════════════════════
# ВЫБОР ТЕМЫ ДНЯ
# ══════════════════════════════════════
def load_manifest():
    if MANIFEST_FILE.exists():
        return json.loads(MANIFEST_FILE.read_text(encoding='utf-8'))
    return {"articles": []}


def pick_topic(manifest):
    day_of_year = datetime.now(timezone.utc).timetuple().tm_yday
    used_topics = {a.get('topic_key', '') for a in manifest['articles']}

    if day_of_year % 2 == 0:
        # Чётный день — реальные новости (веб-поиск)
        return None, 'news'
    else:
        # Нечётный день — вечнозелёная тема, ещё не публиковавшаяся
        available = [t for t in EVERGREEN_TOPICS if t not in used_topics]
        if not available:
            available = EVERGREEN_TOPICS  # если весь пул исчерпан — начинаем по новой
        topic = random.choice(available)
        return topic, 'evergreen'


# ══════════════════════════════════════
# ГЕНЕРАЦИЯ СТАТЬИ ЧЕРЕЗ CLAUDE
# ══════════════════════════════════════
def build_prompt(topic, mode, recent_titles):
    lang_list = ', '.join(f"{code} ({name})" for code, name in LANG_NAMES.items())
    recent_block = ('\n\nDo NOT repeat these recently published topics:\n' + '\n'.join(f'- {t}' for t in recent_titles)) if recent_titles else ''
    today = datetime.now(timezone.utc).strftime('%B %d, %Y')
    current_year = datetime.now(timezone.utc).year

    date_warning = f"""
CRITICAL — today's real date is {today}. Web search results often contain
stale pages from previous years (event announcements, festival dates, price
lists) that were never removed from the internet. Before including ANY date,
festival, event, price, or "happening now" claim in the article:
1. Explicitly check whether the year mentioned in your source matches {current_year}.
2. If a source describes an event from a past year (e.g. "{current_year - 1}"),
   do NOT present it as current or "happening right now" in {current_year}.
   Either search specifically for the {current_year} edition of that event, or
   write about the topic in a way that doesn't falsely imply it's happening today.
3. Never let an outdated year silently slip through — this is the single most
   important accuracy rule for this article."""

    if mode == 'news':
        topic_instruction = f"""Search the web for a genuinely current, real, and specific piece of news or
practical update relevant to tourists visiting Georgia (the country) right now,
in {current_year}
— e.g. a new attraction, a changed regulation, a seasonal event, a transport
change, a price change, weather-related travel advice, or similar. It must be
something a real news search actually surfaced, not invented, AND it must be
genuinely current for {current_year} (see date-accuracy rules below).{recent_block}"""
    else:
        topic_instruction = f"""Write an engaging, factually accurate evergreen article about this specific
topic: "{topic}". Use only real, verifiable facts — do not invent statistics,
dates, or names."""

    return f"""You are writing one article for a Georgia (Caucasus) travel guide website.
{date_warning}

{topic_instruction}

Write the article in ALL of these {len(LANGS)} languages: {lang_list}.

Important: all 9 languages must have genuinely translated, natural-sounding
content (not literal word-for-word translation) — same facts, same structure,
each written as if by a native speaker. The "slug" and "topic_key" stay in
English regardless of content language.

When ready, call the submit_article tool with the finished article."""


def generate_article():
    manifest = load_manifest()
    topic, mode = pick_topic(manifest)
    recent_titles = [a.get('title_en', '') for a in manifest['articles'][-15:]]

    print(f'Mode: {mode}, topic hint: {topic or "(web search)"}')
    prompt = build_prompt(topic, mode, recent_titles)
    # Для режима "news" с веб-поиском нужен больший запас токенов — сам
    # процесс поиска (несколько раундов server_tool_use/web_search_tool_result)
    # тоже расходует токены ДО того, как модель напишет финальный JSON.
    # Без запаса ответ обрывается на середине (stop_reason=max_tokens).
    tokens_for_call = 24000 if mode == 'news' else 14000
    raw = call_claude(prompt, use_web_search=(mode == 'news'), max_tokens=tokens_for_call)
    data = raw  # call_claude уже вернул разобранный dict через tool use

    # Гарантируем уникальность slug (на случай редкого совпадения)
    existing_slugs = {a['slug'] for a in manifest['articles']}
    base_slug = data['slug']
    slug = base_slug
    i = 2
    while slug in existing_slugs:
        slug = f"{base_slug}-{i}"
        i += 1
    data['slug'] = slug

    return data, mode


# ══════════════════════════════════════
# РЕНДЕР HTML-СТРАНИЦЫ СТАТЬИ
# ══════════════════════════════════════
ARTICLE_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>__TITLE_EN__ | sakartvelo.ai</title>
<meta name="description" content="__TAGLINE_EN__">
<link rel="canonical" href="https://sakartvelo.ai/news/__SLUG__/">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,600;1,6..72,500&family=Space+Grotesk:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{
  --accent:__ACCENT__; --accent-deep:__ACCENT_DEEP__; --accent-light:__ACCENT_LIGHT__;
  --paper:#fffdf8; --ink:#241f1a; --muted:#6b6055; --bg:#f4efe4;
  --line:rgba(36,31,26,.1); --radius:18px; --shadow:0 8px 30px rgba(10,50,48,.08);
}
[data-theme=dark]{
  --bg:#0c1615; --paper:#16211f; --ink:#eef0ee; --muted:#9aa8a4;
  --line:rgba(255,255,255,.09); --shadow:0 8px 30px rgba(0,0,0,.35);
}
*{box-sizing:border-box}
body{margin:0;font-family:'Space Grotesk',sans-serif;background:var(--bg);color:var(--ink);-webkit-font-smoothing:antialiased}
h1,h2{font-family:'Newsreader',serif;margin:0}
a{color:inherit}
.wrap{max-width:700px;margin:0 auto;padding:0 20px}
.nav{display:flex;align-items:center;justify-content:space-between;padding:16px 20px;background:var(--paper);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:50}
.logo{font-family:'Newsreader',serif;font-weight:600;font-size:1.15rem;color:var(--accent-deep);text-decoration:none;display:flex;align-items:center;gap:8px}
#tb{border:1px solid var(--line);background:var(--bg);border-radius:100px;width:38px;height:38px;font-size:1rem;cursor:pointer}
.langpick{border:1px solid var(--line);background:var(--bg);border-radius:100px;padding:8px 14px;font-size:.85rem;font-weight:500;display:flex;align-items:center;gap:6px}
select#langSel{border:none;background:transparent;font-family:inherit;font-size:.85rem;font-weight:500;color:var(--ink)}
.hero{background:linear-gradient(180deg,var(--accent-deep) 0%,var(--accent) 60%,var(--accent-light) 100%);color:#fff;padding:44px 20px 56px;text-align:center}
.hero .emoji{font-size:2.6rem;margin-bottom:10px}
.hero h1{font-size:2.1rem;font-weight:600;line-height:1.15;color:#fff}
.hero p{font-size:1rem;color:rgba(255,255,255,.9);margin:12px auto 0;max-width:500px;line-height:1.5}
.hero .date{font-size:.8rem;color:rgba(255,255,255,.7);margin-top:14px}
.section{padding:30px 20px}
.intro{font-size:1.05rem;line-height:1.6;color:var(--ink)}
.block{margin-bottom:26px}
.block h2{font-size:1.25rem;color:var(--accent-deep);margin-bottom:8px}
.block p{font-size:.96rem;line-height:1.65;color:var(--ink);margin:0}
.closing{font-style:italic;color:var(--muted);border-top:1px solid var(--line);padding-top:20px;margin-top:10px}
.bcard{background:linear-gradient(135deg,var(--accent-deep),var(--accent));color:#fff;border-radius:var(--radius);padding:20px;margin:20px 0;text-align:center}
.bcard .t{font-weight:600;font-size:1.05rem;margin-bottom:6px}
.bcard .s{font-size:.85rem;color:rgba(255,255,255,.85);margin-bottom:14px}
.bcard .btn{background:#fff;color:var(--accent-deep);font-weight:600;font-size:.85rem;padding:10px 20px;border-radius:100px;text-decoration:none;display:inline-block}
footer{text-align:center;padding:24px 20px 50px;color:var(--muted);font-size:.78rem}
.backlink{display:block;text-align:center;margin:20px 0;font-size:.85rem;color:var(--accent-deep);font-weight:500;text-decoration:none}

html[data-theme=dark] .nav{background:#16211f !important;border-color:rgba(255,255,255,.09) !important}
html[data-theme=dark] .langpick,html[data-theme=dark] #tb{background:#0c1615 !important;border-color:rgba(255,255,255,.09) !important;color:#eef0ee !important}
html[data-theme=dark] #langSel{color:#eef0ee !important}
html[data-theme=dark] .block p, html[data-theme=dark] .intro{color:#eef0ee !important}
html[data-theme=dark] .closing, html[data-theme=dark] footer{color:#9aa8a4 !important}
</style>
</head>
<body>

<nav class="nav">
  <a href="https://sakartvelo.ai" class="logo">🍶 sakartvelo.ai</a>
  <div style="display:flex;align-items:center;gap:8px">
    <button id="tb" onclick="toggleTheme()">🌙</button>
    <div class="langpick">🌐
      <select id="langSel" onchange="setLang(this.value)">
        <option value="ru">Русский</option>
        <option value="en">English</option>
        <option value="tr">Türkçe</option>
        <option value="ar">العربية</option>
        <option value="he">עברית</option>
        <option value="fa">فارسی</option>
        <option value="de">Deutsch</option>
        <option value="it">Italiano</option>
        <option value="es">Español</option>
      </select>
    </div>
  </div>
</nav>

<div class="hero">
  <div class="emoji">__EMOJI__</div>
  <h1 id="hT">__TITLE_EN__</h1>
  <p id="hS">__TAGLINE_EN__</p>
  <div class="date">__DATE_HUMAN__</div>
</div>

<div class="wrap section">
  <p class="intro" id="introText"></p>
  <div id="sectionsList"></div>
  <p class="closing" id="closingText"></p>

  <div class="bcard">
    <div class="t" id="bcardT">Plan your Georgia trip</div>
    <div class="s" id="bcardS">Hotels, tours and car rental — same price as booking direct</div>
    <a class="btn" id="bcardBtn" href="https://www.booking.com/searchresults.html?aid=7916610&ss=Tbilisi%2C+Georgia&order=bayesian_review_score" target="_blank" rel="noopener">Explore options</a>
  </div>

  <a class="backlink" id="backLink" href="https://sakartvelo.ai/news/">← All articles</a>
</div>

<footer id="footerText">Free AI Travel Guide to Georgia · sakartvelo.ai</footer>

<script>
var D=__DATA_JSON__;

function g(id){return document.getElementById(id)}
function st(id,val){ if(val!=null) g(id).textContent = val }

var BACKLINK_TEXT = {
  ru:'← Все статьи', en:'← All articles', tr:'← Tüm makaleler', ar:'← جميع المقالات',
  he:'← כל המאמרים', fa:'← همه مقالات', de:'← Alle Artikel', it:'← Tutti gli articoli', es:'← Todos los artículos'
};
var BCARD_T = {
  ru:'Спланируйте поездку в Грузию', en:'Plan your Georgia trip', tr:'Gürcistan gezinizi planlayın',
  ar:'خطط لرحلتك إلى جورجيا', he:'תכננו את הטיול שלכם לגאורגיה', fa:'سفر خود به گرجستان را برنامه‌ریزی کنید',
  de:'Plane deine Georgien-Reise', it:'Pianifica il tuo viaggio in Georgia', es:'Planifica tu viaje a Georgia'
};
var BCARD_S = {
  ru:'Отели, туры и аренда авто — та же цена, что при прямом бронировании',
  en:'Hotels, tours and car rental — same price as booking direct',
  tr:'Oteller, turlar ve araç kiralama — doğrudan rezervasyonla aynı fiyat',
  ar:'فنادق وجولات وتأجير سيارات — نفس سعر الحجز المباشر',
  he:'מלונות, טיולים והשכרת רכב — אותו מחיר כמו הזמנה ישירה',
  fa:'هتل، تور و اجاره خودرو — همان قیمت رزرو مستقیم',
  de:'Hotels, Touren und Mietwagen — gleicher Preis wie bei Direktbuchung',
  it:'Hotel, tour e noleggio auto — stesso prezzo della prenotazione diretta',
  es:'Hoteles, tours y alquiler de coches — mismo precio que la reserva directa'
};
var FOOTER_TEXT = {
  ru:'Бесплатный AI-гид по Грузии · sakartvelo.ai', en:'Free AI Travel Guide to Georgia · sakartvelo.ai',
  tr:'Gürcistan için ücretsiz AI Seyahat Rehberi · sakartvelo.ai', ar:'دليل سفر مجاني بالذكاء الاصطناعي لجورجيا · sakartvelo.ai',
  he:'מדריך טיולים חינמי מבוסס AI לגאורגיה · sakartvelo.ai', fa:'راهنمای سفر رایگان هوش‌مصنوعی گرجستان · sakartvelo.ai',
  de:'Kostenloser KI-Reiseführer für Georgien · sakartvelo.ai', it:'Guida di viaggio AI gratuita per la Georgia · sakartvelo.ai',
  es:'Guía de viaje IA gratuita para Georgia · sakartvelo.ai'
};

function setLang(lang){
  var d = D[lang] || D.en;
  var isRTL = (lang==='he' || lang==='ar' || lang==='fa');
  document.documentElement.setAttribute('dir', isRTL ? 'rtl' : 'ltr');
  document.documentElement.setAttribute('lang', lang);

  st('hT', d.title); st('hS', d.tagline); st('introText', d.intro); st('closingText', d.closing);

  var html = '';
  (d.sections || []).forEach(function(s){
    html += '<div class="block"><h2>'+s.heading+'</h2><p>'+s.body+'</p></div>';
  });
  g('sectionsList').innerHTML = html;

  st('backLink', BACKLINK_TEXT[lang] || BACKLINK_TEXT.en);
  st('bcardT', BCARD_T[lang] || BCARD_T.en);
  st('bcardS', BCARD_S[lang] || BCARD_S.en);
  st('footerText', FOOTER_TEXT[lang] || FOOTER_TEXT.en);
  g('bcardBtn').href = 'https://www.booking.com/searchresults.html?aid=7916610&ss=Tbilisi%2C+Georgia&lang='+lang+'&order=bayesian_review_score';

  try{ localStorage.setItem('sakartvelo_lang', lang); }catch(e){}
  document.title = d.title + ' | sakartvelo.ai';
}

function toggleTheme(){
  var dk = document.documentElement.dataset.theme === 'dark';
  document.documentElement.dataset.theme = dk ? '' : 'dark';
  try{ localStorage.setItem('sak_theme', dk ? '' : 'dark'); }catch(e){}
  g('tb').textContent = dk ? '\\ud83c\\udf19' : '\\u2600\\ufe0f';
}
try{
  if(localStorage.getItem('sak_theme')==='dark'){
    document.documentElement.dataset.theme='dark';
    document.addEventListener('DOMContentLoaded', function(){ g('tb').textContent='\\u2600\\ufe0f'; });
  }
}catch(e){}

(function(){
  var saved = 'en';
  try{ saved = localStorage.getItem('sakartvelo_lang') || 'en'; }catch(e){}
  var browserLang = (navigator.language || 'en').slice(0,2);
  if (D[browserLang] && saved === 'en') saved = browserLang;
  var chosen = D[saved] ? saved : 'en';
  document.getElementById('langSel').value = chosen;
  setLang(chosen);
})();
</script>

</body>
</html>
'''


def render_article_html(data, palette, date_human):
    content = data['content']
    en = content.get('en', {})
    html = ARTICLE_TEMPLATE
    html = html.replace('__TITLE_EN__', en.get('title', data['slug']))
    html = html.replace('__TAGLINE_EN__', en.get('tagline', ''))
    html = html.replace('__SLUG__', data['slug'])
    html = html.replace('__EMOJI__', data.get('emoji', '🇬🇪'))
    html = html.replace('__DATE_HUMAN__', date_human)
    html = html.replace('__ACCENT__', palette['accent'])
    html = html.replace('__ACCENT_DEEP__', palette['accent_deep'])
    html = html.replace('__ACCENT_LIGHT__', palette['accent_light'])
    html = html.replace('__DATA_JSON__', json.dumps(content, ensure_ascii=False))
    return html


# ══════════════════════════════════════
# СТРАНИЦА-СПИСОК news/index.html
# ══════════════════════════════════════
INDEX_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Georgia Travel News & Guides | sakartvelo.ai</title>
<meta name="description" content="Latest travel news and guides about Georgia — updated regularly.">
<link href="https://fonts.googleapis.com/css2?family=Newsreader:wght@600&family=Space+Grotesk:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{--ink:#241f1a;--muted:#6b6055;--bg:#f4efe4;--paper:#fffdf8;--line:rgba(36,31,26,.1);--accent:#0e7c78}
*{box-sizing:border-box}
body{margin:0;font-family:'Space Grotesk',sans-serif;background:var(--bg);color:var(--ink)}
h1{font-family:'Newsreader',serif}
.wrap{max-width:700px;margin:0 auto;padding:20px}
.item{display:block;background:var(--paper);border:1px solid var(--line);border-radius:16px;padding:18px;margin-bottom:12px;text-decoration:none;color:var(--ink)}
.item .e{font-size:1.4rem;margin-bottom:6px}
.item .t{font-weight:600;font-size:1.05rem}
.item .d{color:var(--muted);font-size:.8rem;margin-top:4px}
a.back{color:var(--accent);font-weight:500;text-decoration:none}
</style>
</head>
<body>
<div class="wrap">
  <p><a class="back" href="https://sakartvelo.ai">← sakartvelo.ai</a></p>
  <h1>🇬🇪 Georgia Travel News & Guides</h1>
  __ITEMS__
</div>
</body>
</html>
'''


def rebuild_news_index(manifest):
    items_html = ''
    for a in reversed(manifest['articles'][-100:]):
        items_html += (f'<a class="item" href="https://sakartvelo.ai/news/{a["slug"]}/">'
                        f'<div class="e">{a.get("emoji","🇬🇪")}</div>'
                        f'<div class="t">{a.get("title_en","")}</div>'
                        f'<div class="d">{a.get("date","")}</div></a>\n  ')
    html = INDEX_TEMPLATE.replace('__ITEMS__', items_html)
    (NEWS_DIR / 'index.html').write_text(html, encoding='utf-8')


# ══════════════════════════════════════
# MAIN
# ══════════════════════════════════════
def main():
    print(f'Generating article — {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}')

    manifest = load_manifest()
    data, mode = generate_article()

    palette = random.choice(PALETTES)
    date_human = datetime.now(timezone.utc).strftime('%B %d, %Y')

    html = render_article_html(data, palette, date_human)

    article_dir = NEWS_DIR / data['slug']
    article_dir.mkdir(parents=True, exist_ok=True)
    (article_dir / 'index.html').write_text(html, encoding='utf-8')

    manifest['articles'].append({
        'slug': data['slug'],
        'topic_key': data.get('topic_key', data['slug']),
        'title_en': data['content'].get('en', {}).get('title', data['slug']),
        'emoji': data.get('emoji', '🇬🇪'),
        'mode': mode,
        'date': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
    })
    MANIFEST_FILE.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')

    rebuild_news_index(manifest)

    print(f'Published: news/{data["slug"]}/ (mode={mode}, palette={palette["name"]})')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(1)
