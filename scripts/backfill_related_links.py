#!/usr/bin/env python3
"""
Разовый скрипт: добавляет блок "Смотрите также" (внутренняя перелинковка
на geo-страницы, регионы, другие статьи, планировщик) на уже опубликованные
статьи, которые вышли ДО этого улучшения.

Как и backfill_booking_cards.py — ничего не удаляет и не переписывает
существующий контент, только дописывает маленький скрипт в конец страницы,
который вставляет блок ссылок перед "← All articles" после загрузки.

Запускается ОДИН РАЗ (через отдельный workflow вручную).
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from generate_article import build_related_links, NEWS_DIR, MANIFEST_FILE

DATA_RE = re.compile(r'var D=(\{.*\});\s*\nfunction g\(id\)', re.DOTALL)

MARKER = '__RELATED_BACKFILL__'


def process_article(slug):
    path = NEWS_DIR / slug / 'index.html'
    if not path.exists():
        print(f'  ⚠️  {slug}: файл не найден, пропускаю')
        return False

    html = path.read_text(encoding='utf-8')

    if MARKER in html:
        print(f'  ⏭  {slug}: уже пропатчен ранее, пропускаю')
        return False

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
    related_map = build_related_links(en, slug)

    patch = f'''
<script id="{MARKER}">
(function(){{
  var RELATED_OVERRIDE = {json.dumps(related_map, ensure_ascii=False)};
  if (typeof window.setLang === 'function') {{
    var _origSetLang2 = window.setLang;
    window.setLang = function(lang){{
      _origSetLang2(lang);
      var rel = RELATED_OVERRIDE[lang] || RELATED_OVERRIDE.en;
      var target = document.getElementById('backLink');
      if (target && !document.getElementById('relatedLinksBackfill')) {{
        var box = document.createElement('div');
        box.id = 'relatedLinksBackfill';
        box.style.cssText = 'margin:24px 0;padding:18px;border:1px solid var(--line);border-radius:18px';
        var head = document.createElement('div');
        head.style.cssText = 'font-weight:600;font-size:.9rem;color:var(--muted);margin-bottom:10px';
        head.textContent = rel.header;
        box.appendChild(head);
        (rel.links || []).forEach(function(l, i){{
          var a = document.createElement('a');
          a.href = l.url; a.target = '_blank'; a.rel = 'noopener';
          a.textContent = l.label;
          a.style.cssText = 'display:block;padding:8px 0;font-size:.92rem;color:var(--accent-deep);font-weight:500;text-decoration:none;'
            + (i>0 ? 'border-top:1px solid var(--line)' : '');
          box.appendChild(a);
        }});
        target.parentNode.insertBefore(box, target);
      }} else if (target) {{
        var existing = document.getElementById('relatedLinksBackfill');
        var head2 = existing.querySelector('div');
        if (head2) head2.textContent = rel.header;
        var links2 = existing.querySelectorAll('a');
        (rel.links || []).forEach(function(l, i){{
          if (links2[i]) {{ links2[i].textContent = l.label; links2[i].href = l.url; }}
        }});
      }}
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

    html = html.replace('</body>', patch, 1)
    path.write_text(html, encoding='utf-8')

    n_links = len(related_map.get('en', {}).get('links', []))
    print(f'  ✅ {slug}: добавлено {n_links} ссылок')
    return True


def main():
    manifest = json.loads(MANIFEST_FILE.read_text(encoding='utf-8'))
    slugs = [a['slug'] for a in manifest['articles']]
    print(f'Найдено {len(slugs)} статей в manifest.json\n')

    updated = 0
    for slug in slugs:
        if process_article(slug):
            updated += 1

    print(f'\nГотово: обновлено {updated} из {len(slugs)} статей.')


if __name__ == '__main__':
    main()
