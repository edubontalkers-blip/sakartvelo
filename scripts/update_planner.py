#!/usr/bin/env python3
"""
sakartvelo.ai — Daily Planner Data Updater
Runs every day via GitHub Actions to keep data fresh.
"""

import json
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
WARNINGS_FILE = BASE_DIR / 'planner' / 'warnings.json'


def get_season(month):
    if month >= 12 or month <= 2:
        return 'winter'
    if month >= 3 and month <= 5:
        return 'spring'
    if month >= 6 and month <= 8:
        return 'summer'
    return 'autumn'


def get_road_warnings(month):
    warnings = []

    # Tusheti — road open only June to October
    if month < 6 or month > 10:
        warnings.append({
            'level': 'red',
            'text': 'Tusheti is CLOSED — road open only June to October'
        })
    else:
        warnings.append({
            'level': 'green',
            'text': 'Tusheti is OPEN — enjoy the most remote region of Georgia (open June-October)'
        })

    # Batumi swimming season
    if month >= 6 and month <= 9:
        warnings.append({
            'level': 'green',
            'text': 'Batumi Black Sea — swimming season is open'
        })

    # Svaneti — always relevant
    warnings.append({
        'level': 'yellow',
        'text': 'Svaneti — always requires 4WD vehicle for mountain roads'
    })

    # Kazbegi — open year round
    warnings.append({
        'level': 'green',
        'text': 'Kazbegi (Stepantsminda) — open year round'
    })

    # Gudauri — ski season Dec-Apr, hiking in summer
    if month >= 12 or month <= 4:
        warnings.append({
            'level': 'green',
            'text': 'Gudauri — ski season is active, great powder snow'
        })
    else:
        warnings.append({
            'level': 'green',
            'text': 'Gudauri — summer hiking season, ski season December-April'
        })

    return warnings


def get_seasonal_tips(season):
    tips = {
        'winter': [
            'Pack warm clothes — Tbilisi can get cold, mountains are very cold',
            'Gudauri ski resort is perfect this time of year',
            'Many mountain guesthouses close in winter — book in advance',
            'New Year in Tbilisi is spectacular — great atmosphere'
        ],
        'spring': [
            'Best time for hiking — not too hot, everything is green',
            'Wildflowers in Kazbegi and Svaneti are stunning',
            'Wine harvest preparation in Kakheti',
            'Book accommodation early — spring is popular'
        ],
        'summer': [
            'High season July — book hotels 2-3 weeks in advance',
            'Mountains are the best escape from Tbilisi heat in summer',
            'Batumi beach is perfect — warm Black Sea water',
            'Tusheti is open — most remote and beautiful region of Georgia'
        ],
        'autumn': [
            'BEST time to visit — perfect weather, wine harvest',
            'Rtveli festival — grape harvest in Kakheti (October)',
            'Incredible colors in Svaneti forests',
            'Slightly lower prices than summer'
        ]
    }
    return tips.get(season, tips['summer'])


def get_transport_prices(season):
    return {
        'tbilisi_to_kazbegi_taxi': '$60-85 per car',
        'tbilisi_to_kazbegi_marshrutka': '$5-7 per person',
        'tbilisi_to_batumi_train': '$10-15 per person',
        'tbilisi_to_kakheti_marshrutka': '$3-5 per person',
        'tbilisi_metro': '1 GEL per trip (~$0.35)',
        'tbilisi_taxi_in_city': '$2-5 per trip (Yandex/Bolt app)',
        'note': f'Prices approximate for {season} 2026. Verify locally.'
    }


def update_warnings():
    month = datetime.now().month
    season = get_season(month)

    warnings = {
        'updated': datetime.now().isoformat(),
        'season': season,
        'general': [
            {
                'level': 'red',
                'text': 'Travel insurance is MANDATORY from January 2026 — Georgian law. You cannot enter without it.'
            },
            {
                'level': 'yellow',
                'text': 'No ATMs in mountain villages — always bring Georgian Lari cash.'
            },
            {
                'level': 'yellow',
                'text': 'No shops above 2000m altitude — stock up food and water before going to mountains.'
            },
            {
                'level': 'yellow',
                'text': 'Phone signal is unreliable in mountains — download your PDF before going.'
            },
            {
                'level': 'green',
                'text': 'Georgia is a very safe country for tourists.'
            }
        ],
        'road_warnings': get_road_warnings(month),
        'seasonal_tips': get_seasonal_tips(season),
        'transport_prices': get_transport_prices(season)
    }

    WARNINGS_FILE.parent.mkdir(exist_ok=True)
    WARNINGS_FILE.write_text(
        json.dumps(warnings, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )
    print(f'Warnings updated for {season} season (month {month})')


def main():
    print(f'Updating planner data — {datetime.now().strftime("%Y-%m-%d %H:%M UTC")}')
    update_warnings()
    print('Done!')


if __name__ == '__main__':
    main()
