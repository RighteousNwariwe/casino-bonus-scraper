# Casino bonus scraper

Pulls welcome bonus data for three South African online casinos (Hollywoodbets, PlayaBets, Springbok Casino) from one public comparison page, structures it with a language model (Google Gemini, free tier), and shows it on a single responsive web page.

Source: https://betproof.co.za/casino-bonuses-south-africa/

## How it works

1. `scrape.py` reads robots.txt and only continues if the page is allowed. It never retries or works around a block. If the source refuses us, it stops with a clear message so you can switch source.
2. It fetches the page once and parses the "Every SA casino bonus compared" table with BeautifulSoup.
3. For each of the three casinos, the free-text cells (bonus, wagering, minimum deposit) go to a Gemini model, which returns structured fields as JSON constrained by a schema.
4. The script checks that every number the model returned really appears in the source text, and retries if not. This guards against invented values.
5. The result is written to `site/casinos.json`, and `site/index.html` renders it as a table (cards on phones).

Output fields per casino: `welcome_bonus`, `wagering_multiplier`, `min_deposit` (amount and currency), `source_url`. A short `wagering_note`, the casino's review page URL and the raw source text are included for traceability.

## Install

Requires Python 3.9 or newer.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```bash
export GEMINI_API_KEY="your-key-here"   # Windows PowerShell: $env:GEMINI_API_KEY="your-key-here"
python scrape.py
```

This writes `site/casinos.json`.

Get a free key at https://aistudio.google.com (no billing needed). The script defaults to the `gemini-flash-latest` model alias. If Google renames or retires it, set `GEMINI_MODEL` to a current Flash model name from AI Studio.

To preview the page locally (a local server is needed because the page loads the JSON with fetch):

```bash
python -m http.server 8000 --directory site
```

Then open http://localhost:8000

## Tests

```bash
python -m unittest discover -s tests -v
```

The tests run offline with a fake model. They cover table parsing, the number validation and the retry logic.

## Deploy

Netlify: drag the `site` folder onto https://app.netlify.com/drop, or connect the repo (`netlify.toml` already sets the publish directory to `site`). GitHub Pages and Vercel also work with `site` as the root.

## Known limits

- The parser depends on the table headers (Casino, Bonus, Wagering, Min Deposit). If the source redesigns the table, the script raises a clear error instead of writing bad data.
- Where a casino lists several multipliers (for example "1x sports / 10x spins"), the headline `wagering_multiplier` is the first one listed, and `wagering_note` says what it applies to.
- The comparison page states that its terms were last verified on a given date, so the data can be older than today.
