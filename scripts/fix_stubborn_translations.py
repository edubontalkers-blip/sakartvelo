#!/usr/bin/env python3
"""
Точечный разовый скрипт: чинит ТОЛЬКО те несколько мест, которые уже
несколько раз подряд не поддавались обычному repair_translations.py
(видно по логам — одни и те же статьи/языки регулярно упираются в
"sections пришло не списком, а str").

В отличие от repair_translations.py, этот скрипт НЕ проверяет все 39
статей заново — работает по короткому известному списку, поэтому быстрый
и недорогой. Использует более надёжный, но чуть более медленный способ
перевода — по одному кусочку текста за раз (translate_field_by_field),
а не всю статью одним большим запросом.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from generate_article import translate_field_by_field, validate_translation, LANG_NAMES, NEWS_DIR

DATA_RE = re.compile(r'var D=(\{.*\});\s*\nfunction g\(id\)', re.DOTALL)

# Известные упрямые места — собраны из логов повторных запусков
# repair_translations.py, где эти пары регулярно не поддавались.
STUBBORN_SPOTS = [
    ('khinkali-etiquette-how-georgians-eat-dumplings', 'de'),
    ('tbilisi-tripadvisor-trending-destination-2026', 'de'),
    ('uplistsikhe-ancient-rock-hewn-city', 'tr'),
    ('uplistsikhe-ancient-rock-hewn-city', 'he'),
    ('uplistsikhe-ancient-rock-hewn-city', 'de'),
    ('batumi-black-sea-georgia-summer-2026', 'de'),
    ('georgian-felt-hats-svan-wool-crafts', 'tr'),
    ('georgian-felt-hats-svan-wool-crafts', 'de'),
    ('kvesheti-kobi-tunnel-georgia-mountain-travel-2026', 'de'),
    ('davit-gareja-cave-monastery-azerbaijan-border', 'tr'),
    ('davit-gareja-cave-monastery-azerbaijan-border', 'de'),
    ('planet-hollywood-tbilisi-integrated-resort-2026', 'ar'),
    ('planet-hollywood-tbilisi-integrated-resort-2026', 'de'),
    ('georgian-alphabet-unique-script', 'de'),
    ('batumi-black-sea-georgia-summer-guide', 'de'),
]


def commit_progress(slug, lang):
    try:
        subprocess.run(['git', 'add', 'news/'], check=False)
        diff = subprocess.run(['git', 'diff', '--cached', '--quiet'])
        if diff.returncode != 0:
            subprocess.run(['git', 'commit', '-m', f'Fix stubborn translation: {slug} [{lang}]'], check=False)
            subprocess.run(['git', 'push'], check=False)
            print(f'  💾 {slug} [{lang}]: сохранено (commit + push)')
    except Exception as e:
        print(f'  ⚠️  не удалось сохранить прогресс — {e}')


def fix_one(slug, lang):
    path = NEWS_DIR / slug / 'index.html'
    if not path.exists():
        print(f'  ⚠️  {slug}: файл не найден, пропускаю')
        return False

    html = path.read_text(encoding='utf-8')
    m = DATA_RE.search(html)
    if not m:
        print(f'  ⚠️  {slug}: не нашёл встроенные данные статьи — пропускаю')
        return False

    try:
        content = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        print(f'  ⚠️  {slug}: не смог разобрать JSON — {e}')
        return False

    en = content.get('en', {})
    if not en:
        print(f'  ⚠️  {slug}: нет английского оригинала — пропускаю')
        return False

    lang_name = LANG_NAMES[lang]

    # Проверяем, не готово ли это место уже — например, с прошлого
    # (прерванного) запуска. Без этой проверки скрипт слепо переводил бы
    # заново даже то, что уже успешно исправлено, тратя время и деньги
    # впустую при каждом повторном запуске.
    existing = content.get(lang)
    if existing and not validate_translation(en, existing, lang_name):
        print(f'  ⏭  {slug} [{lang_name}]: уже готово, пропускаю')
        return False

    print(f'  🔧 {slug} [{lang_name}]: перевожу по кусочку (надёжный способ)...')
    translated = translate_field_by_field(en, lang_name)
    content[lang] = translated

    new_data_json = json.dumps(content, ensure_ascii=False)
    html = DATA_RE.sub(lambda mo: 'var D=' + new_data_json + ';\nfunction g(id)', html, count=1)
    path.write_text(html, encoding='utf-8')
    print(f'  ✅ {slug} [{lang_name}]: переведено и записано')
    return True


def main():
    print(f'Точечная починка {len(STUBBORN_SPOTS)} известных упрямых мест...\n')
    fixed = 0
    for slug, lang in STUBBORN_SPOTS:
        if fix_one(slug, lang):
            fixed += 1
            commit_progress(slug, lang)
    print(f'\nГотово: исправлено {fixed} из {len(STUBBORN_SPOTS)}.')


if __name__ == '__main__':
    main()
