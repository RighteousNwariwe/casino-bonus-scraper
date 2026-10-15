# Submission notes

Live page: https://YOUR-SITE.netlify.app
Repo: https://github.com/RighteousNwariwe/YOUR-REPO

## Approach (short note)

I scrape the comparison table from BetProof, check robots.txt first, and parse the rows with BeautifulSoup. A language model then converts the free-text bonus, wagering and deposit cells into structured fields through a forced tool call, which gives me typed output instead of text I have to parse. Because models can invent numbers, every number it returns is checked against the source text, and the call is retried if it does not match. The output is one JSON file, and a static HTML page renders it, with the table turning into cards on small screens.

## Taking this to production: 200 casinos, updated daily

**Where it runs.** One small VPS at Hetzner (2 vCPU, 4 GB RAM), running Docker Compose with three containers: the scraper, PostgreSQL and Caddy. Two hundred pages a day is a tiny workload, so a single cheap server is enough and keeps the bill and the moving parts low. I would move to managed services only when traffic or team size justifies it. The public site stays static and is served from Cloudflare Pages, so a server outage never takes the page down.

**Database.** PostgreSQL. Tables for casinos, bonus snapshots (one row per casino per day, so I can see what changed) and scrape runs. Backups: a nightly `pg_dump` pushed to Backblaze B2, keeping 14 daily and 8 weekly copies, plus weekly server snapshots. I would test a restore once a month, because an untested backup is only a hope.

**Domain, DNS and HTTPS.** Domain registered at Cloudflare Registrar, DNS on Cloudflare. HTTPS comes from Cloudflare for the static site and from Caddy's automatic Let's Encrypt certificates for anything served from the VPS, with HTTP redirected to HTTPS.

**Scheduling and failure alerts.** A systemd timer runs the scraper at 03:00 UTC. It respects robots.txt, waits between requests and only calls the language model when a casino's raw text has changed since yesterday, which keeps cost down. When a run finishes cleanly it pings Healthchecks.io. If no ping arrives by a set time, I get an email or Telegram alert, so even a crashed server is noticed. Errors also go to Sentry. Before publishing, the script checks the results (for example, no more than 5 percent of casinos with missing fields). If a run looks wrong, yesterday's data stays live and I get an alert.

**Rough monthly cost.** About 6 EUR for the VPS, 1 to 2 USD for backup storage, 1 USD for the domain (about 12 USD a year), and a few USD for model calls (less with the change detection). Cloudflare, Healthchecks and Sentry are on free tiers. Roughly 10 to 15 USD a month in total. Prices are approximate and should be checked before ordering.

## AI use

1. I used Claude (Anthropic) to write the scraper, tests, page and README.
2. The model is also used inside the scraper, to turn free-text bonus terms into structured fields.
3. One place the AI was wrong: [WRITE YOUR OWN REAL EXAMPLE HERE. What it got wrong, and how you caught it.]
4. I reviewed every file, ran the tests and ran the scraper myself against the live page before submitting.
5. I can explain every part of the code, including why each choice was made.

## Time taken

[FILL IN: actual hours you spent, including reading the code and deploying.]
