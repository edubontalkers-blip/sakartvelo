#!/usr/bin/env python3
"""
Разовый скрипт: добавляет умный блок бронирования (с конкретным местом
и честным текстом — тот же, что теперь автоматически ставится на новые
статьи) на уже опубликованные статьи, которые вышли ДО этого улучшения.

Важно: ничего не удаляет и не переписывает существующий контент статьи.
Просто дописывает маленький скрипт в конец страницы, который после
загрузки подменяет текст/ссылку блока бронирования на правильные —
основано на том же анализе текста статьи (detect_place/detect_booking_type),
что и в generate_article.py.

Запускается ОДИН РАЗ (через отдельный workflow вручную), не входит
в ежедневный цикл генерации статей.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from generate_article import build_bcard_map, NEWS_DIR, MANIFEST_FILE

# Ищем встроенные данные статьи: var D={...};\nfunction g(id)
DATA_RE = re.compile(r'var D=(\{.*\});\s*\nfunction g\(id\)', re.DOTALL)

MARKER = '__BOOKING_BACKFILL__'
# Находит уже существующий патч целиком (от открывающего <script id="..."> до
# закрывающего </body> в самом конце файла) — чтобы можно было БЕЗОПАСНО
# перезаписать его свежей версией, если логика генерации улучшилась
# (например, добавился перевод названий мест), а не просто пропускать
# уже пропатченные статьи навсегда.
EXISTING_PATCH_RE = re.compile(
    r'\n<script id="' + re.escape(MARKER) + r'">.*?</script>\s*</body>',
    re.DOTALL
)


def process_article(slug):
    path = NEWS_DIR / slug / 'index.html'
    if not path.exists():
        print(f'  ⚠️  {slug}: файл не найден, пропускаю')
        return False

    html = path.read_text(encoding='utf-8')
    already_patched = MARKER in html

    m = DATA_RE.search(html)
    if not m:
        print(f'  ⚠️  {slug}: не нашёл встроенные данные статьи (нестандартная структура) — пропускаю')
        return False

    try:
        content = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        print(f'  ⚠️  {slug}: не смог разобрать JSON — {e}')
        return False

    en = content.get('en', {})
    bcard_map = build_bcard_map(en)

    patch = f'''
<script id="{MARKER}">
(function(){{
  var BCARD_OVERRIDE = {json.dumps(bcard_map, ensure_ascii=False)};
  if (typeof window.setLang === 'function') {{

    var _origSetLang = window.setLang;
    window.setLang = function(lang){{
      _origSetLang(lang);
      var bc = BCARD_OVERRIDE[lang] || BCARD_OVERRIDE.en;
      var t = document.getElementById('bcardT');
      var s = document.getElementById('bcardS');
      var btn = document.getElementById('bcardBtn');
      if (t) t.textContent = bc.t;
      if (s) s.textContent = bc.s;
      if (btn) {{ btn.textContent = bc.btn; btn.href = bc.link; }}
    }};
    var currentLang = document.documentElement.getAttribute('lang') || 'en';
    window.setLang(currentLang);
  }}
}})();
</script>
</body>'''

    if '</body>' not in html:
        print(f'  ⚠️  {slug}: не нашёл закрывающий тег </body> — пропускаю')
        return False

    if already_patched:
        # Убираем старую версию патча целиком, чтобы не копить дубликаты
        # скрипта при каждом повторном запуске — оставляем чистый </body>
        html = EXISTING_PATCH_RE.sub('\n</body>', html, count=1)

    html = html.replace('</body>', patch, 1)
    path.write_text(html, encoding='utf-8')

    place_preview = bcard_map.get('en', {}).get('t', '?')
    action = 'обновлён' if already_patched else 'добавлен'
    print(f'  ✅ {slug}: {action} — "{place_preview}"')
    return True


def main():
    manifest = json.loads(MANIFEST_FILE.read_text(encoding='utf-8'))
    slugs = [a['slug'] for a in manifest['articles']]
    print(f'Найдено {len(slugs)} статей в manifest.json\n')

    updated = 0
    for slug in slugs:
        if process_article(slug):
            updated += 1

    print(f'\nГотово: обновлено {updated} из {len(slugs)} статей '
          f'(остальные — уже пропатчены раньше или пропущены из-за нестандартной структуры).')


if __name__ == '__main__':
    main()
