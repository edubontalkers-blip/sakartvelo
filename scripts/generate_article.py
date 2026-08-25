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
import urllib.parse
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
    {"name": "turquoise",   "accent": "#0e7c78", "accent_deep": "#0a5a57", "accent_light": "#4fd8cf"},
    {"name": "forest",      "accent": "#2d6a4f", "accent_deep": "#1b4332", "accent_light": "#74c69d"},
    {"name": "amber",       "accent": "#b45309", "accent_deep": "#7c3a05", "accent_light": "#f5b25e"},
    {"name": "plum",        "accent": "#7c3a6d", "accent_deep": "#54244a", "accent_light": "#c98cb9"},
    {"name": "slate",       "accent": "#3d5a73", "accent_deep": "#25384a", "accent_light": "#8fb3cc"},
    {"name": "terracotta",  "accent": "#b8562f", "accent_deep": "#7f3a1f", "accent_light": "#e79a72"},
    {"name": "wine",        "accent": "#7a1e33", "accent_deep": "#4f1220", "accent_light": "#c96c80"},
    {"name": "olive",       "accent": "#5c6b1f", "accent_deep": "#3a4413", "accent_light": "#a3b661"},
    {"name": "indigo",      "accent": "#38427a", "accent_deep": "#232a52", "accent_light": "#8891c9"},
    {"name": "copper",      "accent": "#8a5a2e", "accent_deep": "#5c3a1a", "accent_light": "#d1a05f"},
    {"name": "teal_deep",   "accent": "#0b5566", "accent_deep": "#073a46", "accent_light": "#5aa8b8"},
    {"name": "rose",        "accent": "#9c3f5c", "accent_deep": "#692a3d", "accent_light": "#d692a6"},
]

# Лёгкая вариация формы (углы карточек/кнопок) поверх смены цвета — так
# соседние статьи не выглядят одним перекрашенным шаблоном. Меняем только
# то, что безопасно параметризовано в CSS (--radius), не трогая структуру
# вёрстки, чтобы не рисковать сломать отображение непроверенным изменением.
RADII = ["10px", "18px", "26px"]

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

SUBMIT_ARTICLE_DRAFT_TOOL = {
    "name": "submit_article_draft",
    "description": "Submit the finished article draft in English.",
    "input_schema": {
        "type": "object",
        "properties": {
            "slug": {"type": "string", "description": "Short URL-safe English slug, no spaces"},
            "topic_key": {"type": "string", "description": "Short stable English key for de-duplication"},
            "emoji": {"type": "string", "description": "Single emoji representing the topic"},
            "content": ARTICLE_LANG_SCHEMA
        },
        "required": ["slug", "topic_key", "emoji", "content"]
    }
}

SUBMIT_TRANSLATION_TOOL = {
    "name": "submit_translation",
    "description": "Submit the translated article content.",
    "input_schema": ARTICLE_LANG_SCHEMA
}


# ══════════════════════════════════════
# ANTHROPIC API (raw HTTPS, без сторонних библиотек)
# ══════════════════════════════════════
def call_claude(prompt, tool, use_web_search=False, max_tokens=16000):
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        raise RuntimeError('ANTHROPIC_API_KEY is not set')

    tools = [tool]
    if use_web_search:
        tools.append({"type": "web_search_20250305", "name": "web_search", "max_uses": 5})

    body = {
        "model": "claude-sonnet-4-6",
        "max_tokens": max_tokens,
        "stream": True,
        "tools": tools,
        # Не форсируем tool_choice — модели нужно свободно пользоваться
        # web_search сначала, и только потом вызвать нужный инструмент.
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

    target_name = tool['name']
    for _, b in sorted(blocks.items()):
        if b['type'] == 'tool_use' and b.get('name') == target_name:
            if stop_reason == 'max_tokens':
                raise RuntimeError(
                    f'Ответ обрезан по лимиту токенов (max_tokens={max_tokens}) ДО того, как '
                    f'модель закончила вызов {target_name} — JSON гарантированно неполный.'
                )
            return json.loads(b['text'])

    raise RuntimeError(f'{target_name} tool was not called. Blocks: ' + json.dumps(block_types))


def call_claude_with_retry(prompt, tool, use_web_search=False, max_tokens=16000, attempts=2):
    """Небольшая обёртка с автоповтором — генерация немного случайна,
    поэтому если один запрос не удался (обрыв по токенам, редкая ошибка
    формата), почти всегда помогает просто попробовать ещё раз."""
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            return call_claude(prompt, tool, use_web_search=use_web_search, max_tokens=max_tokens)
        except RuntimeError as e:
            last_error = e
            print(f'Попытка {attempt}/{attempts} не удалась: {e}')
    raise last_error


def call_claude_with_retry_and_parse(prompt, tool, use_web_search=False, max_tokens=16000,
                                      attempts=3, parse_field=None):
    """Как call_claude_with_retry, но дополнительно защищает от редкого
    случая, когда модель кладёт вложенный объект (например, 'content')
    не как настоящий JSON-объект, а как JSON-текст ВНУТРИ строки —
    и этот текст оказывается чуть кривым (не хватает запятой/скобки).

    Раньше эта проверка стояла ПОСЛЕ call_claude_with_retry и падала
    без единой попытки повтора — одна кривая генерация роняла весь скрипт.
    Теперь любая проблема (сетевая, обрыв по токенам, кривой вложенный
    JSON) уходит в один и тот же цикл повторов."""
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            result = call_claude(prompt, tool, use_web_search=use_web_search, max_tokens=max_tokens)
            if parse_field is not None:
                value = result.get(parse_field)
                if isinstance(value, str):
                    value = json.loads(value)  # может бросить JSONDecodeError — поймаем ниже
                if not isinstance(value, dict):
                    raise RuntimeError(
                        f'Поле "{parse_field}" не объект и не строка-JSON: {type(value)}'
                    )
                result[parse_field] = value
            return result
        except (RuntimeError, json.JSONDecodeError) as e:
            last_error = e
            print(f'Попытка {attempt}/{attempts} не удалась: {e}')
    raise last_error


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

Write the article in English.

When ready, call the submit_article_draft tool with the finished article."""


def build_translation_prompt(english_content, lang_name):
    n_sections = len(english_content.get('sections', []))
    return f"""You are a professional native-speaker translator specializing in travel and
tourism content, translating for a published, edited travel guide website —
not a rough draft. Translate the following Georgia (Caucasus) travel article
from English into {lang_name}.

CRITICAL COMPLETENESS REQUIREMENT — read before calling the tool:
- The English original has exactly {n_sections} sections. Your translation
  MUST have exactly {n_sections} sections too — never fewer, never merged,
  never summarized down.
- Before calling submit_translation, silently re-read your own output and
  compare it field-by-field against the English original: title, tagline,
  intro, every single section (heading + body), and closing. If any field is
  noticeably shorter than its English counterpart, that is a sign you have
  cut it short — go back and translate it in full before submitting.
- A shortened, summarized, or partially-omitted translation is a failure
  even if the JSON is technically valid. Completeness matters more than
  brevity.

Quality bar — this must read as if it were originally written in {lang_name}
by a professional travel writer, not translated:
- Never translate word-for-word. Rephrase idioms, sentence rhythm, and word
  order the way a native {lang_name} speaker naturally would.
- Use natural, idiomatic phrasing and the register appropriate for a
  polished travel publication (warm, engaging, precise) — not stiff or
  overly literal.
- Preserve every fact exactly: numbers, prices, dates, proper names, place
  names. Do not invent, drop, or alter any factual detail.
- Keep the same structure and the same number of sections as the original —
  do not add or remove content, only translate it.
- Grammar, spelling, and punctuation must be flawless native-level
  {lang_name}, as if reviewed by a professional copy editor.

Article to translate (JSON):
{json.dumps(english_content, ensure_ascii=False)}

When ready, call the submit_translation tool with the translated content."""


def validate_translation(en_content, translated, lang_name):
    """Строгая проверка: не просто 'пришёл валидный JSON', а 'действительно
    есть весь текст, ничего не обрезано и не пропущено'. Возвращает список
    найденных проблем (пустой список = всё хорошо)."""
    problems = []
    if not isinstance(translated, dict):
        return [f'{lang_name}: перевод вообще не объект ({type(translated)})']

    for field in ('title', 'tagline', 'intro', 'closing'):
        val = (translated.get(field) or '').strip()
        if not val:
            problems.append(f'{lang_name}: пустое поле "{field}"')
        elif len(val) < 0.25 * len(en_content.get(field, '')):
            # Резкое сокращение относительно оригинала почти всегда значит,
            # что перевод обрубился на середине, а не что он "просто короткий"
            problems.append(f'{lang_name}: поле "{field}" подозрительно короткое '
                             f'(похоже на обрыв — {len(val)} симв. против {len(en_content.get(field, ""))} в оригинале)')

    en_sections = en_content.get('sections', [])
    tr_sections = translated.get('sections', [])
    if len(tr_sections) != len(en_sections):
        problems.append(f'{lang_name}: разделов {len(tr_sections)}, а должно быть {len(en_sections)} (как в английском)')
    else:
        for i, (en_s, tr_s) in enumerate(zip(en_sections, tr_sections)):
            heading = (tr_s.get('heading') or '').strip()
            body = (tr_s.get('body') or '').strip()
            if not heading or not body:
                problems.append(f'{lang_name}: раздел {i+1} — пустой заголовок или текст')
            elif len(body) < 0.25 * len(en_s.get('body', '')):
                problems.append(f'{lang_name}: раздел {i+1} подозрительно короткий (обрыв?) — '
                                 f'{len(body)} симв. против {len(en_s.get("body", ""))}')
    return problems


def translate_with_validation(en_content, lang, lang_name, attempts=4):
    """Переводит на один язык и проверяет результат на полноту. Если
    перевод неполный/обрублен — пробует снова (до `attempts` раз), а не
    просто принимает первый ответ, прошедший базовую JSON-валидацию.
    Если после всех попыток так и не получилось — используем английский
    текст как безопасный запасной вариант для ЭТОГО языка (лучше показать
    англ. текст, чем пустую/обрубленную страницу), но громко пишем в лог,
    чтобы это было заметно, а не потерялось."""
    tr_prompt = build_translation_prompt(en_content, lang_name)
    for attempt in range(1, attempts + 1):
        translated = call_claude_with_retry_and_parse(
            tr_prompt, SUBMIT_TRANSLATION_TOOL,
            use_web_search=False, max_tokens=12000,
            parse_field=None
        )
        problems = validate_translation(en_content, translated, lang_name)
        if not problems:
            return translated, False
        print(f'  ⚠️ Попытка {attempt}/{attempts}: перевод на {lang_name} неполный:')
        for p in problems:
            print(f'      - {p}')
    print(f'  🛑 {lang_name}: перевод так и не получился полным за {attempts} попыток. '
          f'Использую английский текст как запасной вариант для этого языка, '
          f'чтобы страница не вышла с обрывом или пустыми блоками.')
    return en_content, True


def generate_article():
    manifest = load_manifest()
    topic, mode = pick_topic(manifest)
    recent_titles = [a.get('title_en', '') for a in manifest['articles'][-15:]]

    print(f'Mode: {mode}, topic hint: {topic or "(web search)"}')

    # ── Шаг 1: пишем черновик ТОЛЬКО на английском (с веб-поиском, если news) ──
    # Один язык — маленький, предсказуемый объём, обрыв по токенам практически
    # исключён даже с умеренным лимитом. Веб-поиску (только тут) даём побольше
    # запаса, так как сам процесс поиска тоже расходует токены.
    draft_prompt = build_prompt(topic, mode, recent_titles)
    draft_tokens = 24000 if mode == 'news' else 12000
    draft = call_claude_with_retry_and_parse(
        draft_prompt, SUBMIT_ARTICLE_DRAFT_TOOL,
        use_web_search=(mode == 'news'), max_tokens=draft_tokens,
        parse_field='content'
    )

    print(f'Черновик на английском готов: "{draft["content"].get("title", "?")}"')

    # ── Шаг 2: переводим на остальные 8 языков — по одному языку за запрос ──
    # Каждый перевод — маленький, независимый запрос. Без веб-поиска, без
    # накопления контекста нескольких языков в одном ответе — практически
    # невозможно упереться в лимит токенов даже без максимального запаса.
    content_all = {'en': draft['content']}
    fallback_used = {}
    for lang in LANGS:
        if lang == 'en':
            continue
        print(f'Перевод на {LANG_NAMES[lang]}...')
        content_all[lang], fallback_used[lang] = translate_with_validation(
            draft['content'], lang, LANG_NAMES[lang]
        )

    # Финальная сводка по всем 9 языкам — сразу видно в логе GitHub Actions,
    # если что-то вышло неполным (в частности немецкий — по нему отдельно
    # просили быть уверенными на 100%).
    print('\n=== Проверка полноты по всем языкам ===')
    all_ok = True
    for lang in LANGS:
        if lang == 'en' or not fallback_used.get(lang):
            print(f'  {LANG_NAMES[lang]:12s}: ✅ полный, непрерванный текст')
        else:
            print(f'  {LANG_NAMES[lang]:12s}: ⚠️ перевод не удался за все попытки — стоит английский текст вместо него')
            all_ok = False
    if not all_ok:
        print('Внимание: не все языки перевелись идеально, но статья публикуется — '
              'там, где перевод не получился, стоит английский текст вместо '
              'пустого/обрубленного блока.')
    else:
        print('Все 9 языков — полный, непрерванный текст. ✅')

    data = {
        'slug': draft['slug'],
        'topic_key': draft.get('topic_key', draft['slug']),
        'emoji': draft.get('emoji', '🇬🇪'),
        'content': content_all
    }

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
<script type="application/ld+json">__JSONLD__</script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,600;1,6..72,500&family=Space+Grotesk:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{
  --accent:__ACCENT__; --accent-deep:__ACCENT_DEEP__; --accent-light:__ACCENT_LIGHT__;
  --paper:#fffdf8; --ink:#241f1a; --muted:#6b6055; --bg:#f4efe4;
  --line:rgba(36,31,26,.1); --radius:__RADIUS__; --shadow:0 8px 30px rgba(10,50,48,.08);
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
    <div class="t" id="bcardT">Thinking about visiting Georgia?</div>
    <div class="s" id="bcardS">We put together a few honest options — same price as booking direct.</div>
    <a class="btn" id="bcardBtn" href="https://www.booking.com/searchresults.html?aid=7916610&ss=Tbilisi%2C+Georgia&order=bayesian_review_score" target="_blank" rel="noopener">See options</a>
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
var BCARD = __BCARD_JSON__;
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
  var bc = BCARD[lang] || BCARD.en;
  st('bcardT', bc.t); st('bcardS', bc.s);
  g('bcardBtn').textContent = bc.btn;
  g('bcardBtn').href = bc.link;
  st('footerText', FOOTER_TEXT[lang] || FOOTER_TEXT.en);

  try{ localStorage.setItem('sak_lang', lang); }catch(e){}
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
  try{ saved = localStorage.getItem('sak_lang') || 'en'; }catch(e){}
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


def build_jsonld(data, date_iso):
    """Структурированная разметка (schema.org/Article) — не видна
    посетителю, но помогает поисковикам и ИИ-ответам (Google AI Overview,
    ChatGPT, Perplexity) понять и процитировать статью. В 2026 году это
    особенно важно: большинство сайтов, которых цитирует AI Overview, НЕ
    входят в топ-10 обычной выдачи — попадание туда идёт в основном через
    правильную разметку, а не только через позицию."""
    en = data['content'].get('en', {})
    return {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": en.get('title', data['slug']),
        "description": en.get('tagline', ''),
        "datePublished": date_iso,
        "dateModified": date_iso,
        "inLanguage": "en",
        "author": {"@type": "Organization", "name": "sakartvelo.ai"},
        "publisher": {
            "@type": "Organization",
            "name": "sakartvelo.ai",
            "logo": {"@type": "ImageObject", "url": "https://sakartvelo.ai/apple-touch-icon.png"}
        },
        "mainEntityOfPage": {
            "@type": "WebPage",
            "@id": f"https://sakartvelo.ai/news/{data['slug']}/"
        },
    }


def render_article_html(data, palette, radius, date_human, date_iso):
    content = data['content']
    en = content.get('en', {})
    bcard_map = build_bcard_map(en)
    jsonld = build_jsonld(data, date_iso)
    html = ARTICLE_TEMPLATE
    html = html.replace('__TITLE_EN__', en.get('title', data['slug']))
    html = html.replace('__TAGLINE_EN__', en.get('tagline', ''))
    html = html.replace('__SLUG__', data['slug'])
    html = html.replace('__EMOJI__', data.get('emoji', '🇬🇪'))
    html = html.replace('__DATE_HUMAN__', date_human)
    html = html.replace('__ACCENT__', palette['accent'])
    html = html.replace('__ACCENT_DEEP__', palette['accent_deep'])
    html = html.replace('__ACCENT_LIGHT__', palette['accent_light'])
    html = html.replace('__RADIUS__', radius)
    html = html.replace('__DATA_JSON__', json.dumps(content, ensure_ascii=False))
    html = html.replace('__BCARD_JSON__', json.dumps(bcard_map, ensure_ascii=False))
    html = html.replace('__JSONLD__', json.dumps(jsonld, ensure_ascii=False))
    return html


# ══════════════════════════════════════
# УМНЫЙ БЛОК БРОНИРОВАНИЯ (в конец каждой статьи)
# ══════════════════════════════════════
# Идея: не один и тот же общий баннер "Тбилиси" на каждой статье, а блок,
# который называет именно то место, о котором статья, и звучит как совет
# друга, а не реклама. Честность (та же цена, что напрямую) — это то, что
# отличает "заботу" от "впаривания": человек видит, что его не пытаются
# обмануть, и это единственное, что реально снижает отторжение.

# Места, для которых имеет смысл предлагать бронирование (у остальных тем,
# вроде "грузинский алфавит" или "хинкали-этикет", прямой ценности от
# ссылки на отель нет — для них остаётся общий, не привязанный к городу блок)
BOOKABLE_PLACES = [
    ('Tbilisi',   ['tbilisi']),
    ('Batumi',    ['batumi', 'adjara', 'black sea georgia']),
    ('Kazbegi',   ['kazbegi', 'stepantsminda', 'gergeti']),
    ('Kutaisi',   ['kutaisi', 'prometheus cave', 'bagrati']),
    ('Svaneti',   ['svaneti', 'mestia', 'ushba', 'svan tower']),
    ('Borjomi',   ['borjomi']),
    ('Gudauri',   ['gudauri', 'kobi', 'kvesheti']),
    ('Bakuriani', ['bakuriani']),
    ('Sighnaghi', ['sighnaghi', 'kakheti', 'alaverdi', 'kvevri', 'wine region']),
    ('Vardzia',   ['vardzia']),
    ('Mtskheta',  ['mtskheta', 'jvari monastery']),
    ('Ureki',     ['ureki']),
]


def detect_place(en):
    """Ищет в английском черновике упоминание конкретного 'бронируемого'
    места. Возвращает название места или None, если статья про абстрактную
    тему (алфавит, кухня, история) без явной привязки к городу.

    Сначала смотрим только заголовок (самый надёжный сигнал темы статьи).
    Если там ничего не нашлось — считаем упоминания по всему тексту и
    берём то место, которое встречается чаще всего (а не первое попавшееся
    по порядку в списке), чтобы статьи-маршруты вроде "Тбилиси → Батуми"
    не всегда цеплялись за первый упомянутый город."""
    title = en.get('title', '').lower()
    body = ' '.join([
        en.get('tagline', ''), en.get('intro', ''), en.get('closing', ''),
        ' '.join(s.get('heading', '') + ' ' + s.get('body', '')
                  for s in en.get('sections', []))
    ]).lower()

    # Заголовок весит больше (это самый надёжный сигнал темы), но не
    # решает всё единолично — если тело статьи явно про другой город
    # (как в статьях-маршрутах "Тбилиси → Батуми"), это тоже учитывается.
    best_place, best_score = None, 0
    for place, keywords in BOOKABLE_PLACES:
        score = 3 * sum(title.count(kw) for kw in keywords) + sum(body.count(kw) for kw in keywords)
        if score > best_score:
            best_place, best_score = place, score
    return best_place


def detect_booking_type(en):
    """Грубая эвристика: о чём скорее статья — трансфер/дорога, экскурсия
    или (по умолчанию) проживание. Определяет, какая партнёрка уместнее."""
    text = (en.get('title', '') + ' ' + en.get('tagline', '')).lower()
    if any(k in text for k in ('transfer', 'drive', 'road trip', 'highway',
                                'rental car', 'car rental')):
        return 'transfer'
    if any(k in text for k in ('tour', 'excursion', 'day trip', 'hike',
                                'hiking', 'trek', 'guided')):
        return 'tour'
    return 'hotel'


def build_booking_link(place, booking_type, lang):
    q = urllib.parse.quote(f'{place}, Georgia' if place else 'Tbilisi, Georgia')
    if booking_type == 'tour':
        return f'https://www.viator.com/searchResults/all?text={q}&pid=P00056692'
    if booking_type == 'transfer':
        return f'https://www.rentalcars.com/SearchResults.do?affiliateCode=travelpayouts732753&city={q}'
    return (f'https://www.booking.com/searchresults.html?aid=7916610'
            f'&ss={q}&lang={lang}&order=bayesian_review_score')


# Тёплый, честный тон: не "купи сейчас", а "вот что я нашёл, цена та же,
# что напрямую" — раз человек уже читал именно про это место, упоминание
# по имени звучит как забота, а не как случайная реклама.
PLACE_BCARD_T = {
    'ru': 'Собираетесь в {place}?',
    'en': 'Thinking about visiting {place}?',
    'tr': '{place}\'a gitmeyi düşünüyor musun?',
    'ar': 'هل تفكر في زيارة {place}؟',
    'he': 'חושבים לבקר ב-{place}?',
    'fa': 'به {place} فکر می‌کنی؟',
    'de': 'Denkst du an einen Besuch in {place}?',
    'it': 'Stai pensando di visitare {place}?',
    'es': '¿Piensas visitar {place}?',
}
PLACE_BCARD_S = {
    'ru': 'Собрали честные варианты — та же цена, что при прямом бронировании, просто чтобы вам не искать самим.',
    'en': 'We put together a few honest options — same price as booking direct, just to save you the searching.',
    'tr': 'Dürüst seçenekleri bir araya getirdik — doğrudan rezervasyonla aynı fiyat, sadece aramanızı kolaylaştırmak için.',
    'ar': 'جمعنا لك خيارات صادقة — بنفس سعر الحجز المباشر، فقط لنوفر عليك عناء البحث.',
    'he': 'ריכזנו כמה אפשרויות הוגנות — באותו מחיר כמו הזמנה ישירה, רק כדי לחסוך לכם את החיפוש.',
    'fa': 'چند گزینه صادقانه جمع کردیم — با همان قیمت رزرو مستقیم، فقط برای اینکه جست‌وجو نکنید.',
    'de': 'Wir haben ein paar ehrliche Optionen zusammengestellt — gleicher Preis wie bei Direktbuchung, nur um dir die Suche zu ersparen.',
    'it': 'Abbiamo raccolto alcune opzioni oneste — stesso prezzo della prenotazione diretta, solo per risparmiarti la ricerca.',
    'es': 'Reunimos algunas opciones honestas — mismo precio que la reserva directa, solo para ahorrarte la búsqueda.',
}
PLACE_BCARD_BTN = {
    'ru': 'Посмотреть варианты', 'en': 'See options', 'tr': 'Seçenekleri gör',
    'ar': 'عرض الخيارات', 'he': 'לצפייה באפשרויות', 'fa': 'مشاهده گزینه‌ها',
    'de': 'Optionen ansehen', 'it': 'Vedi opzioni', 'es': 'Ver opciones',
}


def build_bcard_map(en_content):
    """Собирает готовый словарь {lang: {t, s, btn, link}} на стороне
    Python — так и в HTML попадает уже финальный, конкретный текст,
    а не общий шаблон на все статьи подряд. Если статья абстрактная
    (без конкретного места, например про алфавит) — используем мягкий
    общий текст про Грузию целиком, а не выдумываем несуществующее место."""
    place = detect_place(en_content)
    booking_type = detect_booking_type(en_content)
    display_place = place or 'Georgia'
    result = {}
    for lang in list(LANG_NAMES.keys()) + ['en']:
        t = PLACE_BCARD_T.get(lang, PLACE_BCARD_T['en']).format(place=display_place)
        s = PLACE_BCARD_S.get(lang, PLACE_BCARD_S['en'])
        btn = PLACE_BCARD_BTN.get(lang, PLACE_BCARD_BTN['en'])
        result[lang] = {
            't': t, 's': s, 'btn': btn,
            'link': build_booking_link(place, booking_type, lang),
        }
    return result


# ══════════════════════════════════════
# СТРАНИЦА-СПИСОК news/index.html
# ══════════════════════════════════════
INDEX_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Georgia Travel News & Guides | sakartvelo.ai</title>
<meta name="description" content="Latest travel news and guides about Georgia — updated regularly.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,600;1,6..72,500&family=Space+Grotesk:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{--ink:#241f1a;--muted:#6b6055;--bg:#f4efe4;--paper:#fffdf8;--line:rgba(36,31,26,.1);--accent:#0e7c78}
[data-theme=dark]{--bg:#0c1615;--paper:#16211f;--ink:#eef0ee;--muted:#9aa8a4;--line:rgba(255,255,255,.09);--accent:#4fd8cf}
*{box-sizing:border-box}
body{margin:0;font-family:'Space Grotesk',sans-serif;background:var(--bg);color:var(--ink);-webkit-font-smoothing:antialiased}
h1{font-family:'Newsreader',serif;margin:0}
.nav{display:flex;align-items:center;justify-content:space-between;padding:16px 20px;background:var(--paper);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:50}
.logo{font-family:'Newsreader',serif;font-weight:600;font-size:1.15rem;color:var(--accent);text-decoration:none;display:flex;align-items:center;gap:8px}
#tb{border:1px solid var(--line);background:var(--bg);border-radius:100px;width:38px;height:38px;font-size:1rem;cursor:pointer}
.langpick{border:1px solid var(--line);background:var(--bg);border-radius:100px;padding:8px 14px;font-size:.85rem;font-weight:500;display:flex;align-items:center;gap:6px}
select#langSel{border:none;background:transparent;font-family:inherit;font-size:.85rem;font-weight:500;color:var(--ink)}
.wrap{max-width:700px;margin:0 auto;padding:24px 20px}
.item{display:block;background:var(--paper);border:1px solid var(--line);border-radius:16px;padding:18px;margin-bottom:12px;text-decoration:none;color:var(--ink)}
.item .e{font-size:1.4rem;margin-bottom:6px}
.item .t{font-weight:600;font-size:1.05rem}
.item .d{color:var(--muted);font-size:.8rem;margin-top:4px}
html[data-theme=dark] .nav{background:#16211f !important;border-color:rgba(255,255,255,.09) !important}
html[data-theme=dark] .langpick,html[data-theme=dark] #tb{background:#0c1615 !important;border-color:rgba(255,255,255,.09) !important;color:#eef0ee !important}
html[data-theme=dark] #langSel{color:#eef0ee !important}
html[data-theme=dark] .item{background:#16211f !important;border-color:rgba(255,255,255,.09) !important}
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
<div class="wrap">
  <h1 id="pageTitle" style="margin-bottom:18px;font-size:1.6rem">🇬🇪 Georgia Travel News & Guides</h1>
  <div id="itemsList"></div>
</div>
<script>
var ARTICLES=__ARTICLES_JSON__;
var PAGE_TITLE = {
  ru:'🇬🇪 Новости и статьи о Грузии', en:'🇬🇪 Georgia Travel News & Guides', tr:'🇬🇪 Gürcistan Seyahat Haberleri',
  ar:'🇬🇪 أخبار ومقالات السفر في جورجيا', he:'🇬🇪 חדשות וטיולים בגאורגיה', fa:'🇬🇪 اخبار و مقالات سفر گرجستان',
  de:'🇬🇪 Georgien Reisenachrichten', it:'🇬🇪 Notizie di viaggio Georgia', es:'🇬🇪 Noticias de viaje Georgia'
};
function g(id){return document.getElementById(id)}
function setLang(lang){
  var isRTL = (lang==='he' || lang==='ar' || lang==='fa');
  document.documentElement.setAttribute('dir', isRTL ? 'rtl' : 'ltr');
  document.documentElement.setAttribute('lang', lang);
  g('pageTitle').textContent = PAGE_TITLE[lang] || PAGE_TITLE.en;
  var html = '';
  ARTICLES.forEach(function(a){
    var title = (a.titles && a.titles[lang]) || a.title_en || a.slug;
    html += '<a class="item" href="https://sakartvelo.ai/news/'+a.slug+'/">'
      + '<div class="e">'+(a.emoji||'🇬🇪')+'</div>'
      + '<div class="t">'+title+'</div>'
      + '<div class="d">'+(a.date||'')+'</div></a>\\n';
  });
  g('itemsList').innerHTML = html;
  try{ localStorage.setItem('sak_lang', lang); }catch(e){}
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
  try{ saved = localStorage.getItem('sak_lang') || 'en'; }catch(e){}
  var browserLang = (navigator.language || 'en').slice(0,2);
  if (!['ru','en','tr','ar','he','fa','de','it','es'].includes(saved)) saved = 'en';
  if (saved === 'en' && ['ru','tr','ar','he','fa','de','it','es'].indexOf(browserLang) !== -1) saved = browserLang;
  document.getElementById('langSel').value = saved;
  setLang(saved);
})();
</script>
</body>
</html>
'''


def rebuild_news_index(manifest):
    articles_for_js = []
    for a in reversed(manifest['articles'][-100:]):
        articles_for_js.append({
            'slug': a['slug'],
            'emoji': a.get('emoji', '🇬🇪'),
            'date': a.get('date', ''),
            'title_en': a.get('title_en', a['slug']),
            'titles': a.get('titles', {'en': a.get('title_en', a['slug'])}),
        })
    html = INDEX_TEMPLATE.replace('__ARTICLES_JSON__', json.dumps(articles_for_js, ensure_ascii=False))
    (NEWS_DIR / 'index.html').write_text(html, encoding='utf-8')


# ══════════════════════════════════════
# MAIN
# ══════════════════════════════════════
def main():
    print(f'Generating article — {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}')

    manifest = load_manifest()
    data, mode = generate_article()

    palette = random.choice(PALETTES)
    radius = random.choice(RADII)
    date_human = datetime.now(timezone.utc).strftime('%B %d, %Y')
    date_iso = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    html = render_article_html(data, palette, radius, date_human, date_iso)

    article_dir = NEWS_DIR / data['slug']
    article_dir.mkdir(parents=True, exist_ok=True)
    (article_dir / 'index.html').write_text(html, encoding='utf-8')

    manifest['articles'].append({
        'slug': data['slug'],
        'topic_key': data.get('topic_key', data['slug']),
        'title_en': data['content'].get('en', {}).get('title', data['slug']),
        'titles': {lang: data['content'].get(lang, {}).get('title', data['slug']) for lang in LANGS},
        'emoji': data.get('emoji', '🇬🇪'),
        'mode': mode,
        'date': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
    })
    MANIFEST_FILE.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')

    rebuild_news_index(manifest)

    print(f'Published: news/{data["slug"]}/ (mode={mode}, palette={palette["name"]}, radius={radius})')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(1)
