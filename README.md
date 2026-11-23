# Franchise & FOCO Instagram Reel Digest

Daily email with new Instagram reels about franchise opportunities. FOCO
(Franchise Owned, Company Operated) posts are listed first. Each reel is
screened for signs of a genuine offer versus hype or scams.

**Pipeline:** Apify `apify/instagram-hashtag-scraper` → dedupe + 48h freshness
→ red-flag phrase scan → Gemini screening (model type, brand, investment,
genuineness 0–10, red flags) → HTML email via Gmail. It runs on GitHub
Actions every day at 07:47 IST.

## Setup (about 15 min)

1. **Apify**: sign up at apify.com, then copy your API token
   (Settings → API & Integrations).
2. **Gemini API key**: create one free in Google AI Studio. This is optional.
   Without it, a crude keyword heuristic is used instead. Free-tier limits apply
   per project. If a call fails or hits a limit, that batch falls back to the heuristic.
3. **Gmail App Password**: turn on 2-Step Verification on your Google account,
   then create an App Password (Google Account → Security → App passwords).
4. **GitHub**: create a **private** repo, push these files, then add these
   repository secrets (Settings → Secrets and variables → Actions):
   `APIFY_TOKEN`, `GEMINI_API_KEY`, `GMAIL_USER`, `GMAIL_APP_PASSWORD`,
   `DIGEST_TO` (optional, comma-separated).
5. Go to the Actions tab → "Franchise reel digest" → **Run workflow** to test.

## Local test

```bash
pip install -r requirements.txt
python digest.py --dry-run sample_data.json   # writes digest.html, sends nothing
```

## Tuning (`config.yaml`)
- `hashtags` / `keywords`: what to search for.
- `results_per_term`: cost scales with this. Check Apify's current pricing.
- `min_genuineness_score`: the cut-off for the main list.
- `red_flag_phrases`: any match caps the score at 4 and moves the reel to "Flagged".

## Limits, please read
- The score is based **only on the caption**. Nothing is verified. Before
  investing, check the brand's official site, the franchise agreement, the
  company's registration, and talk to existing franchisees.
- Scraping Instagram may conflict with Instagram's Terms of Service.
  Scraper coverage is partial, and actors can break when Instagram changes.
- The Apify input fields (`hashtags`, `keywordSearch`, `resultsType`,
  `resultsLimit`) and output fields were taken from the actor's page in
  Nov 2026. If a run fails, check the actor's current input schema.
- GitHub can disable scheduled workflows in repos with no activity for 60 days.
  Committing `seen.json` each run usually keeps the repo active.

## WhatsApp later (optional)
Add a `send_whatsapp()` function that uses Twilio or the WhatsApp Cloud API,
and call it next to `send_email()`.
