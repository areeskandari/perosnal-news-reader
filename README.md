# Intel Feed — AI & VC Intelligence Blog

A self-hosted intelligence aggregator that crawls 17+ RSS feeds daily,
generates TL;DRs via OpenRouter AI, auto-categorizes, scores articles 1–10,
extracts media, and serves a fast dark-themed frontend.

## Quick Start

### 1. Clone & install

```bash
git clone https://github.com/yourname/intel-blog
cd intel-blog/crawler
pip install -r requirements.txt
```

### 2. Set your OpenRouter API key

Get a free key at https://openrouter.ai (free models available)

```bash
export OPENROUTER_API_KEY="sk-or-..."
```

### 3. Run the crawler

```bash
cd crawler
python crawler.py
python export_json.py
```

### 4. Open the frontend

```bash
# Just open the HTML file directly — no server needed
open ../web/public/index.html

# Or serve it:
cd ../web/public && python -m http.server 3000
```

---

## Automated Daily Crawl

### Option A: GitHub Actions (recommended — free)

1. Push this repo to GitHub
2. Add `OPENROUTER_API_KEY` to Settings → Secrets → Actions
3. The workflow runs automatically at 06:00 and 14:00 UTC daily
4. Results are committed to `web/public/data/` and auto-deploy via Vercel

### Option B: VPS crontab (your `madar` server)

```bash
# Edit crontab
crontab -e

# Add these lines (runs at 07:00 and 15:00 Tehran time = UTC+3:30)
30 3 * * * cd /path/to/intel-blog/crawler && OPENROUTER_API_KEY=sk-or-... python crawler.py >> /var/log/intel-crawler.log 2>&1
30 3 * * * cd /path/to/intel-blog/crawler && python export_json.py >> /var/log/intel-crawler.log 2>&1
```

---

## Adding New Feeds

Edit `crawler/crawler.py`, add to the `FEEDS` list:

```python
{"name": "Your Source", "url": "https://example.com/feed", "category_hint": "ai-research"},
```

Category hints: `ai-research`, `llm-models`, `vc-deals`, `crypto-web3`,
`policy-reg`, `founders`, `mena-gulf`, `frontier-tech`, `enterprise-ai`,
`consumer`, `deep-tech`, `infra-devtools`, `product-growth`,
`safety-alignment`, `open-source`, `investing-ops`

---

## Deploy Frontend to Vercel

```bash
# Install Vercel CLI
npm i -g vercel

# Deploy from web/public
cd web/public
vercel --prod
```

Or connect the GitHub repo to Vercel — it auto-deploys on every push.

---

## Cost

| Service         | Cost     | Notes                          |
|-----------------|----------|--------------------------------|
| GitHub Actions  | Free     | 2000 min/month free            |
| OpenRouter AI   | ~$0–5/mo | Free models available (Gemma)  |
| Vercel          | Free     | Hobby tier                     |
| **Total**       | **~$0–5/mo** |                            |

---

## Data Files (web/public/data/)

| File              | Contents                        |
|-------------------|---------------------------------|
| `articles.json`   | Last 200 articles with metadata |
| `stats.json`      | Aggregate stats                 |
| `sources.json`    | Per-source article counts       |
| `categories.json` | Per-category counts             |
| `must_reads.json` | Top scored must-reads           |
# perosnal-news-reader
