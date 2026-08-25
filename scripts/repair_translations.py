#!/usr/bin/env python3
"""
Разовый скрипт: чинит уже опубликованные статьи, у которых перевод на
каком-то языке (чаще всего немецкий — но проверяются все 8) оказался
неполным — начинается на нужном языке, а потом обрывается и продолжается
остатком английского текста, или просто короче/беднее оригинала.

В отличие от backfill_booking_cards.py и backfill_related_links.py, этот
скрипт РЕАЛЬНО обращается к Anthropic API (нужен ANTHROPIC_API_KEY в
секретах workflow), чтобы заново получить полный, качественный перевод —
а не просто донастроить оформление. Ничего в generate_article.py не
меняет и не трогает саму логику генерации новых статей — только чинит
уже сохранённый текст старых.

Использует ту же проверку полноты (validate_translation) и тот же
защищённый перевод с автоповтором (translate_with_validation), что и
generate_article.py — язык считается "сломанным" по тем же правилам.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from generate_article import (
    validate_translation, translate_with_validation,
    LANGS, LANG_NAMES, NEWS_DIR, MANIFEST_FILE
)

DATA_RE = re.compile(r'var D=(\{.*\});\s*\nfunction g\(id\)', re.DOTALL)


def commit_progress(slug):
    """Сохраняет исправление ЭТОЙ статьи прямо сейчас, отдельным коммитом —
    а не ждёт, пока починятся вообще все статьи. Так деньги, потраченные
    на уже готовый перевод, не пропадают зря, даже если весь процесс
    прервётся на середине (таймаут, отмена, сбой сети) — то, что уже
    исправлено, уже сохранено на сайте."""
    try:
        subprocess.run(['git', 'add', 'news/'], check=False)
        diff = subprocess.run(['git', 'diff', '--cached', '--quiet'])
        if diff.returncode != 0:  # есть что коммитить
            subprocess.run(['git', 'commit', '-m', f'Repair translations: {slug}'], check=False)
            subprocess.run(['git', 'push'], check=False)
            print(f'  💾 {slug}: сохранено (commit + push)')
    except Exception as e:
        print(f'  ⚠️  {slug}: не удалось сохранить прогресс — {e}')


def process_article(slug, problem_spots):
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
        print(f'  ⚠️  {slug}: нет английского оригинала для сверки — пропускаю')
        return False

    changed = False
    for lang in LANGS:
        if lang == 'en':
            continue
        lang_content = content.get(lang)
        problems = (validate_translation(en, lang_content, LANG_NAMES[lang])
                    if lang_content else ['отсутствует вовсе'])
        if not problems:
            continue

        print(f'  🔧 {slug} [{LANG_NAMES[lang]}]: найдены проблемы — перевожу заново:')
        for p in problems:
            print(f'      - {p}')
        # attempts=1 — здесь специально не повторяем по 4 раза каждую
        # проблему: цель сейчас быстро пройтись по ВСЕМ статьям и увидеть
        # полный список мест, где перевод не задался, а не тратить время
        # и деньги на одну и ту же зависающую комбинацию статья+язык.
        # К отмеченным (🛑) местам вернёмся отдельно после этого прохода.
        fixed, fallback = translate_with_validation(en, lang, LANG_NAMES[lang], attempts=1)
        content[lang] = fixed
        changed = True
        if fallback:
            print(f'  🛑 {slug} [{LANG_NAMES[lang]}]: даже сейчас не получилось перевести полностью — оставлен английский текст')
            problem_spots.append((slug, LANG_NAMES[lang]))
        else:
            print(f'  ✅ {slug} [{LANG_NAMES[lang]}]: перевод исправлен и полный')

    if not changed:
        print(f'  ⏭  {slug}: все языки уже в порядке, пропускаю')
        return False

    new_data_json = json.dumps(content, ensure_ascii=False)
    html = DATA_RE.sub(lambda mo: 'var D=' + new_data_json + ';\nfunction g(id)', html, count=1)
    path.write_text(html, encoding='utf-8')
    return True


def main():
    manifest = json.loads(MANIFEST_FILE.read_text(encoding='utf-8'))
    slugs = [a['slug'] for a in manifest['articles']]
    print(f'Проверяю {len(slugs)} статей на всех 8 языках перевода...\n')

    problem_spots = []
    updated = 0
    for slug in slugs:
        if process_article(slug, problem_spots):
            updated += 1
            commit_progress(slug)

    print(f'\nГотово: исправлено {updated} из {len(slugs)} статей '
          f'(остальные уже были в порядке).')

    if problem_spots:
        print(f'\n=== Список мест, куда нужно вернуться отдельно ({len(problem_spots)}) ===')
        for slug, lang in problem_spots:
            print(f'  - {slug} [{lang}]')
    else:
        print('\nВсе проблемные места удалось исправить с первого раза — возвращаться некуда. ✅')


if __name__ == '__main__':
    main()
