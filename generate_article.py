#!/usr/bin/env python3
"""
sakartvelo.ai — Daily Article Generator
Generates SEO articles about Georgia using Claude API
and publishes them as static HTML files.
"""

import os
import json
import re
from datetime import datetime
from pathlib import Path
import anthropic

# ── Configuration ──────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent
GEO_DIR = BASE_DIR / "geo"
SITEMAP_PATH = BASE_DIR / "sitemap.xml"
SITE_URL = "https://sakartvelo.ai"

# ── Topics list (will rotate, skipping already published) ──
TOPICS = [
    {"slug": "tbilisi-old-town-guide", "title": "Tbilisi Old Town — Complete Visitor Guide", "keywords": "Tbilisi Old Town, Abanotubani, sulfur baths, Narikala fortress"},
    {"slug": "best-georgian-wine-2026", "title": "Best Georgian Wines to Try in 2026", "keywords": "Georgian wine, Saperavi, Rkatsiteli, Kakheti, qvevri"},
    {"slug": "kazbegi-hiking-guide", "title": "Kazbegi Hiking Guide — Trails and Tips", "keywords": "Kazbegi hiking, Gergeti Trinity Church, Mount Kazbek, trails"},
    {"slug": "georgian-food-guide", "title": "Georgian Food — Complete Guide for Tourists", "keywords": "Khinkali, Khachapuri, Georgian cuisine, Supra, traditional food"},
    {"slug": "batumi-beach-guide", "title": "Batumi Beach Guide — Everything You Need to Know", "keywords": "Batumi beach, Black Sea, Adjara, subtropical Georgia"},
    {"slug": "georgia-travel-tips-2026", "title": "Georgia Travel Tips 2026 — What You Must Know", "keywords": "Georgia travel tips, visa free, insurance 2026, tourist advice"},
    {"slug": "svaneti-travel-guide", "title": "Svaneti Travel Guide — Medieval Towers and Mountains", "keywords": "Svaneti, Mestia, medieval towers, UNESCO, Caucasus mountains"},
    {"slug": "tbilisi-restaurants-guide", "title": "Best Restaurants in Tbilisi 2026", "keywords": "Tbilisi restaurants, Georgian food, where to eat Tbilisi"},
    {"slug": "georgia-insurance-2026", "title": "Georgia Travel Insurance 2026 — New Law Explained", "keywords": "Georgia insurance law 2026, mandatory insurance, tourist insurance"},
    {"slug": "tbilisi-metro-guide", "title": "Tbilisi Metro — How to Use It as a Tourist", "keywords": "Tbilisi metro, MetroMoney card, public transport Tbilisi"},
    {"slug": "gudauri-ski-guide", "title": "Gudauri Ski Resort — Complete Guide 2026", "keywords": "Gudauri ski, Georgia skiing, Caucasus ski resort, powder snow"},
    {"slug": "borjomi-day-trip", "title": "Borjomi Day Trip from Tbilisi — Complete Guide", "keywords": "Borjomi mineral water, day trip Tbilisi, Borjomi national park"},
    {"slug": "vardzia-cave-city-guide", "title": "Vardzia Cave City — History and Visitor Guide", "keywords": "Vardzia cave city, Queen Tamar, cave monastery Georgia"},
    {"slug": "georgia-visa-free-countries", "title": "Georgia Visa Free — Which Countries Can Enter", "keywords": "Georgia visa free, visa on arrival, travel to Georgia without visa"},
    {"slug": "kakheti-wine-tour", "title": "Kakheti Wine Tour — Best Wineries to Visit", "keywords": "Kakheti wine tour, Georgian winery, wine tasting Georgia"},
    {"slug": "tbilisi-nightlife-guide", "title": "Tbilisi Nightlife Guide — Clubs and Bars 2026", "keywords": "Tbilisi nightlife, Bassiani, clubs Tbilisi, bars Old Town"},
    {"slug": "georgian-alphabet-guide", "title": "Georgian Alphabet — History and How to Read It", "keywords": "Georgian alphabet, Mkhedruli, Georgian script, UNESCO"},
    {"slug": "mtskheta-day-trip", "title": "Mtskheta Day Trip — Ancient Capital of Georgia", "keywords": "Mtskheta, Svetitskhoveli, Jvari monastery, UNESCO Georgia"},
    {"slug": "batumi-hotels-guide", "title": "Best Hotels in Batumi 2026 — Where to Stay", "keywords": "Batumi hotels, where to stay Batumi, accommodation Adjara"},
    {"slug": "georgia-budget-travel", "title": "Georgia Budget Travel Guide — How to Travel Cheap", "keywords": "Georgia budget travel, cheap Georgia, backpacking Georgia"},
    {"slug": "tbilisi-day-trips", "title": "Best Day Trips from Tbilisi 2026", "keywords": "day trips Tbilisi, Mtskheta, Kazbegi, Gori, Kakheti"},
    {"slug": "georgian-chacha-guide", "title": "Chacha — Georgian Grape Vodka Complete Guide", "keywords": "Chacha Georgian vodka, grape vodka, Georgian spirits"},
    {"slug": "georgia-in-winter", "title": "Georgia in Winter — What to Do and Where to Go", "keywords": "Georgia winter travel, skiing Georgia, winter Tbilisi"},
    {"slug": "sighnaghi-city-of-love", "title": "Sighnaghi — City of Love in Kakheti", "keywords": "Sighnaghi, City of Love, Kakheti, Bodbe convent"},
    {"slug": "georgia-for-families", "title": "Georgia with Kids — Family Travel Guide", "keywords": "Georgia family travel, kids Georgia, family friendly Tbilisi"},
    {"slug": "tbilisi-free-things", "title": "Free Things to Do in Tbilisi", "keywords": "free Tbilisi, budget Tbilisi, free attractions Georgia"},
    {"slug": "georgian-tea-culture", "title": "Georgian Tea — History and Where to Try", "keywords": "Georgian tea, Adjara tea, tea plantations Georgia"},
    {"slug": "georgia-photography-spots", "title": "Best Photography Spots in Georgia 2026", "keywords": "Georgia photography, Instagram spots Georgia, best views Tbilisi"},
    {"slug": "prometheus-cave-guide", "title": "Prometheus Cave — Guide to Georgia's Underground Wonder", "keywords": "Prometheus Cave, Kutaisi cave, underground Georgia"},
    {"slug": "georgia-safety-guide", "title": "Is Georgia Safe? — Complete Safety Guide 2026", "keywords": "Georgia safety, is Georgia safe, travel safety Caucasus"},
]


def get_published_slugs():
    """Return set of already published slugs."""
    published = set()
    if GEO_DIR.exists():
        for folder in GEO_DIR.iterdir():
            if folder.is_dir() and (folder / "index.html").exists():
                published.add(folder.name)
    return published


def get_next_topic(published_slugs):
    """Return next unpublished topic."""
    for topic in TOPICS:
        if topic["slug"] not in published_slugs:
            return topic
    # All topics published — create a new variation
    today = datetime.now().strftime("%Y-%m-%d")
    return {
        "slug": f"georgia-travel-guide-{today}",
        "title": f"Georgia Travel Guide — {datetime.now().strftime('%B %Y')}",
        "keywords": "Georgia travel, Tbilisi, tourism Georgia 2026"
    }


def generate_article(topic):
    """Generate article content using Claude API."""
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    prompt = f"""Write a detailed, helpful SEO article about Georgia (the country in the Caucasus) for tourists.

Topic: {topic['title']}
Keywords to include naturally: {topic['keywords']}

Requirements:
- 450-600 words
- Only verified, factual information
- Practical and helpful for tourists visiting Georgia
- Include specific tips, prices, or directions where relevant
- Engaging and natural writing style
- Written in English

Return ONLY valid JSON (no markdown, no backticks):
{{
  "title": "exact article title",
  "description": "meta description under 155 characters",
  "body": "full article text with paragraphs separated by double newline",
  "keywords": "keyword1, keyword2, keyword3, keyword4, keyword5"
}}"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip()
    # Remove markdown code blocks if present
    raw = re.sub(r'^```(?:json)?\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)

    return json.loads(raw)


def build_html(slug, article):
    """Build HTML page from article data."""
    today = datetime.now().strftime("%B %d, %Y")
    today_iso = datetime.now().strftime("%Y-%m-%d")
    canonical = f"{SITE_URL}/geo/{slug}/"

    # Build paragraphs
    paragraphs = ""
    for para in article["body"].split("\n\n"):
        para = para.strip()
        if para:
            paragraphs += f"<p>{para}</p>\n"

    # Build keyword tags
    tags_html = ""
    for kw in article["keywords"].split(","):
        kw = kw.strip()
        if kw:
            kw_slug = re.sub(r'[^a-z0-9]+', '-', kw.lower()).strip('-')
            tags_html += f'<a href="/geo/{kw_slug}/" class="tag">{kw}</a>\n'

    # Structured data
    structured_data = json.dumps({
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": article["title"],
        "description": article["description"],
        "datePublished": today_iso,
        "dateModified": today_iso,
        "url": canonical,
        "author": {
            "@type": "Organization",
            "name": "sakartvelo.ai"
        },
        "publisher": {
            "@type": "Organization",
            "name": "sakartvelo.ai",
            "url": SITE_URL
        }
    }, ensure_ascii=False, indent=2)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{article['title']} | sakartvelo.ai</title>
<meta name="description" content="{article['description']}">
<meta name="keywords" content="{article['keywords']}">
<link rel="canonical" href="{canonical}">
<meta property="og:title" content="{article['title']}">
<meta property="og:description" content="{article['description']}">
<meta property="og:url" content="{canonical}">
<meta property="og:type" content="article">
<meta property="og:site_name" content="sakartvelo.ai">
<script type="application/ld+json">
{structured_data}
</script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
     max-width:680px;margin:0 auto;padding:20px 16px;
     line-height:1.7;color:#1a1a1a;background:#fff}}
header{{display:flex;justify-content:space-between;align-items:center;
        padding-bottom:16px;border-bottom:2px solid #8b1a1a;margin-bottom:24px}}
.logo{{font-weight:900;color:#8b1a1a;text-decoration:none;font-size:1.1rem}}
.back{{background:#8b1a1a;color:#fff;padding:8px 16px;border-radius:8px;
       text-decoration:none;font-weight:700;font-size:.9rem}}
h1{{font-size:1.75rem;font-weight:900;color:#1a0d10;margin-bottom:12px;line-height:1.2}}
.meta{{color:#888;font-size:.85rem;margin-bottom:24px;padding-bottom:16px;
       border-bottom:1px solid #eee}}
p{{margin-bottom:16px;font-size:1.02rem}}
.tags{{display:flex;flex-wrap:wrap;gap:8px;margin-top:24px;padding-top:20px;
       border-top:1px solid #eee}}
.tag{{background:#f5f5f5;border-radius:20px;padding:6px 14px;
      font-size:.82rem;color:#555;text-decoration:none;border:1px solid #e0e0e0}}
.tag:hover{{background:#e8e8e8}}
.related{{margin-top:28px;padding-top:20px;border-top:1px solid #eee}}
.related h3{{font-size:.95rem;color:#666;margin-bottom:12px;font-weight:600}}
.ask{{display:block;background:#c4960a;color:#1a0d10;padding:14px 20px;
      border-radius:12px;text-decoration:none;font-weight:700;
      text-align:center;margin-top:24px;font-size:1rem}}
.ask:hover{{background:#b8880a}}
footer{{margin-top:40px;padding-top:20px;border-top:1px solid #eee;
        color:#aaa;font-size:.8rem;text-align:center}}
footer a{{color:#8b1a1a;text-decoration:none}}
</style>
</head>
<body>

<header>
  <a href="{SITE_URL}" class="logo">🍶 sakartvelo.ai</a>
  <a href="{SITE_URL}" class="back">← Back to site</a>
</header>

<h1>{article['title']}</h1>
<div class="meta">
  🇬🇪 Georgia Travel Guide &nbsp;·&nbsp; Published: {today} &nbsp;·&nbsp; sakartvelo.ai
</div>

{paragraphs}

<div class="tags">
{tags_html}
</div>

<div class="related">
  <h3>Explore Georgia:</h3>
  <a href="/geo/tbilisi/" class="tag">🏙️ Tbilisi</a>
  <a href="/geo/kazbegi/" class="tag">🏔️ Kazbegi</a>
  <a href="/geo/batumi/" class="tag">🌊 Batumi</a>
  <a href="/geo/svaneti/" class="tag">🗻 Svaneti</a>
  <a href="/geo/kakheti/" class="tag">🍷 Kakheti Wine</a>
  <a href="/geo/georgian-cuisine/" class="tag">🍽️ Georgian Food</a>
  <a href="/geo/gudauri/" class="tag">⛷️ Gudauri Ski</a>
  <a href="/geo/borjomi/" class="tag">💧 Borjomi</a>
</div>

<a href="{SITE_URL}" class="ask">🤖 Ask Chacha AI about Georgia</a>

<footer>
  <p>© <a href="{SITE_URL}">sakartvelo.ai</a> — AI Travel Guide to Georgia 🇬🇪</p>
</footer>

</body>
</html>"""

    return html


def update_sitemap(new_slug):
    """Add new URL to sitemap.xml."""
    new_url = f"{SITE_URL}/geo/{new_slug}/"
    today_iso = datetime.now().strftime("%Y-%m-%d")

    new_entry = f"""  <url>
    <loc>{new_url}</loc>
    <lastmod>{today_iso}</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.7</priority>
  </url>"""

    if SITEMAP_PATH.exists():
        content = SITEMAP_PATH.read_text(encoding="utf-8")
        if new_url not in content:
            content = content.replace("</urlset>", f"{new_entry}\n</urlset>")
            SITEMAP_PATH.write_text(content, encoding="utf-8")
            print(f"✅ Sitemap updated with {new_url}")
    else:
        # Create new sitemap
        sitemap = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>{SITE_URL}/</loc>
    <priority>1.0</priority>
    <changefreq>weekly</changefreq>
  </url>
{new_entry}
</urlset>"""
        SITEMAP_PATH.write_text(sitemap, encoding="utf-8")
        print(f"✅ Sitemap created with {new_url}")


def main():
    print("🍶 sakartvelo.ai — Daily Article Generator")
    print(f"📅 Date: {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}")

    # Get next topic
    published = get_published_slugs()
    print(f"📚 Already published: {len(published)} articles")

    topic = get_next_topic(published)
    print(f"📝 Generating: {topic['title']}")

    # Generate article via Claude API
    print("🤖 Calling Claude API...")
    article = generate_article(topic)
    print(f"✅ Article generated: {len(article['body'])} chars")

    # Create output directory
    output_dir = GEO_DIR / topic["slug"]
    output_dir.mkdir(parents=True, exist_ok=True)

    # Build and save HTML
    html = build_html(topic["slug"], article)
    output_file = output_dir / "index.html"
    output_file.write_text(html, encoding="utf-8")
    print(f"✅ Saved: geo/{topic['slug']}/index.html")

    # Update sitemap
    update_sitemap(topic["slug"])

    print(f"🎉 Done! New article live at: {SITE_URL}/geo/{topic['slug']}/")


if __name__ == "__main__":
    main()
