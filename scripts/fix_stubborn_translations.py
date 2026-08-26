name: Fix Stubborn Translations (one-time)

on:
  workflow_dispatch:

jobs:
  fix:
    runs-on: ubuntu-latest
    timeout-minutes: 60
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Configure git
        run: |
          git config user.name "Chacha Bot"
          git config user.email "bot@sakartvelo.ai"

      - name: Fix stubborn translations
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: python3 -u scripts/fix_stubborn_translations.py

      - name: Final safety-net commit
        run: |
          git add news/
          git diff --cached --quiet || git commit -m "Fix stubborn translations (final pass)"
          git push || true
