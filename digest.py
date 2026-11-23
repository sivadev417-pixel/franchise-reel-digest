#!/usr/bin/env python3
"""
Franchise & FOCO Instagram reel digest.

Pipeline: Apify (fetch reels) -> dedupe + freshness filter -> local red-flag scan
          -> Gemini screening (relevance, model type, genuineness) -> HTML email.

Env vars:
  APIFY_TOKEN          Apify API token
  GEMINI_API_KEY       Google Gemini API key (optional; falls back to keyword heuristic)
  GMAIL_USER           Gmail address that sends the digest
  GMAIL_APP_PASSWORD   Gmail App Password (not your normal password)
  DIGEST_TO            Recipient(s), comma separated (defaults to GMAIL_USER)

Usage:
  python digest.py                         # live run
  python digest.py --dry-run sample_data.json   # no Apify, no email; writes digest.html
"""
import argparse
import html
import json
import os
import re
import smtplib
import sys
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import yaml

ROOT = Path(__file__).parent
SEEN_FILE = ROOT / "seen.json"
ACTOR_ID = "apify/instagram-hashtag-scraper"


# ---------------------------------------------------------------- fetch
def fetch_reels(cfg):
    from apify_client import ApifyClient

    client = ApifyClient(os.environ["APIFY_TOKEN"])
    items = []
    runs = []
    if cfg.get("hashtags"):
        runs.append({"hashtags": cfg["hashtags"], "keywordSearch": False})
    if cfg.get("keywords"):
        runs.append({"hashtags": cfg["keywords"], "keywordSearch": True})
    for base in runs:
        run_input = {**base, "resultsType": "reels", "resultsLimit": cfg["results_per_term"]}
        print(f"Apify run: {run_input}")
        run = client.actor(ACTOR_ID).call(run_input=run_input)
        # apify-client <2 returns a dict; 2.x+ returns a Run model
        ds_id = run["defaultDatasetId"] if isinstance(run, dict) else run.default_dataset_id
        items.extend(client.dataset(ds_id).iterate_items())
    return items


# ---------------------------------------------------------------- filter
def parse_ts(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def load_seen():
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()


def save_seen(seen):
    # keep the file bounded
    SEEN_FILE.write_text(json.dumps(sorted(seen)[-5000:]))


def prefilter(items, cfg, seen):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=cfg["max_age_hours"])
    out, ids = [], set()
    for it in items:
        key = it.get("shortCode") or it.get("id") or it.get("url")
        if not key or key in seen or key in ids:
            continue
        ts = parse_ts(it.get("timestamp"))
        if ts and ts < cutoff:
            continue
        ids.add(key)
        caption = (it.get("caption") or "").strip()
        low = caption.lower()
        flags = [p for p in cfg["red_flag_phrases"] if re.search(r"\b" + re.escape(p) + r"\b", low)]
        out.append({
            "key": key,
            "url": it.get("url") or f"https://www.instagram.com/reel/{key}/",
            "owner": it.get("ownerUsername") or "",
            "caption": caption[:1200],
            "timestamp": it.get("timestamp"),
            "views": it.get("videoPlayCount") or it.get("videoViewCount") or 0,
            "likes": it.get("likesCount") if (it.get("likesCount") or 0) >= 0 else None,
            "comments": it.get("commentsCount") or 0,
            "local_flags": flags,
        })
    return out


# ---------------------------------------------------------------- AI screening
PROMPT = """You screen Instagram reel captions for someone researching GENUINE business
franchise opportunities, especially the FOCO model (Franchise Owned, Company Operated:
the investor funds the outlet, the brand runs operations and shares revenue/profit).

For EACH post return an object with:
  key: copy from input
  relevant: true only if it is about a franchise/business-ownership opportunity
  model: one of "FOCO", "FOFO", "COCO", "FICO", "Other franchise", "Unclear"
  brand: brand name if stated, else ""
  investment: investment/ROI figures exactly as stated in the caption, else ""
  genuineness: integer 0-10. Higher = concrete, checkable details (named brand,
     outlet count, investment breakup, agreement terms, official contact/website).
     Lower = vague hype, guaranteed/fixed returns, pressure tactics, MLM/"join my team",
     course-selling disguised as franchise, no brand named.
  red_flags: short list of concerns ([] if none)
  summary: one plain sentence on what is offered

You only see the caption, so you cannot verify anything; score what the caption shows.
Return ONLY a JSON array, no prose.

POSTS:
"""


def ai_screen(posts, cfg):
    import time
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    results = {}
    bs = cfg["ai"]["batch_size"]
    for i in range(0, len(posts), bs):
        batch = [{"key": p["key"], "owner": p["owner"], "caption": p["caption"]} for p in posts[i:i + bs]]
        try:
            resp = client.models.generate_content(
                model=cfg["ai"]["model"],
                contents=PROMPT + json.dumps(batch, ensure_ascii=False),
                config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.2),
            )
            text = resp.text or ""
            m = re.search(r"\[.*\]", text, re.S)
            for r in json.loads(m.group(0) if m else text):
                results[r["key"]] = r
        except Exception as e:  # rate limit, parse error, etc. -> heuristic for this batch
            print(f"WARN: AI batch {i // bs} failed ({e}); using heuristic for it", file=sys.stderr)
            results.update(heuristic_screen(posts[i:i + bs]))
        time.sleep(cfg["ai"].get("pause_seconds", 5))  # stay under free-tier per-minute limits
    return results


def heuristic_screen(posts):
    """Fallback when no GEMINI_API_KEY: crude keyword scoring."""
    res = {}
    for p in posts:
        c = p["caption"].lower()
        relevant = "franchise" in c or "foco" in c
        model = "FOCO" if ("foco" in c or "company operated" in c) else ("FOFO" if "fofo" in c else "Unclear")
        score = 5
        score += 1 if re.search(r"(investment|₹|rs\.?|lakh|lac|cr\b)", c) else 0
        score += 1 if re.search(r"(outlets?|stores?|branches)", c) else 0
        score += 1 if re.search(r"(www\.|\.com|\.in\b)", c) else 0
        res[p["key"]] = {"key": p["key"], "relevant": relevant, "model": model, "brand": "",
                         "investment": "", "genuineness": score, "red_flags": [],
                         "summary": "(heuristic, no AI key set)"}
    return res


def merge(posts, screened, cfg):
    good, flagged = [], []
    for p in posts:
        s = screened.get(p["key"])
        if not s or not s.get("relevant"):
            continue
        score = int(s.get("genuineness", 0))
        flags = list(dict.fromkeys((s.get("red_flags") or []) + [f'mentions "{f}"' for f in p["local_flags"]]))
        if p["local_flags"]:
            score = min(score, 4)
        row = {**p, **s, "genuineness": score, "red_flags": flags}
        (good if score >= cfg["min_genuineness_score"] else flagged).append(row)
    key = lambda r: (r["model"] == "FOCO", r["genuineness"], r["views"] or 0)
    return sorted(good, key=key, reverse=True), sorted(flagged, key=key, reverse=True)


# ---------------------------------------------------------------- email
def row_html(r):
    e = html.escape
    badge = {"FOCO": "#0a7d32"}.get(r["model"], "#555")
    flags = "".join(f"<li>{e(f)}</li>" for f in r["red_flags"])
    stats = f'{r["views"]:,} views' if r["views"] else ""
    return f"""
<tr><td style="padding:12px;border-bottom:1px solid #eee;font-family:Arial,sans-serif;font-size:14px">
  <span style="background:{badge};color:#fff;padding:2px 8px;border-radius:10px;font-size:12px">{e(r["model"])}</span>
  <b style="margin-left:6px">{e(r.get("brand") or "Brand not named")}</b>
  <span style="color:#888"> · score {r["genuineness"]}/10 · @{e(r["owner"])} {('· ' + stats) if stats else ''}</span><br>
  <div style="margin:6px 0">{e(r.get("summary", ""))}</div>
  {f'<div><b>Investment stated:</b> {e(r["investment"])}</div>' if r.get("investment") else ''}
  {f'<ul style="color:#b00;margin:4px 0">{flags}</ul>' if flags else ''}
  <a href="{e(r["url"])}">Open reel</a>
</td></tr>"""


def build_html(good, flagged, cfg):
    date = datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime("%d %b %Y")
    parts = [f'<h2 style="font-family:Arial">Franchise &amp; FOCO reels — {date}</h2>',
             f'<p style="font-family:Arial;color:#555">{len(good)} likely-genuine, {len(flagged)} flagged. '
             'Scores reflect the caption only, not verification. Check the brand\'s official website, '
             'agreement and existing franchisees before investing.</p>',
             '<table width="100%" cellspacing="0">']
    parts += [row_html(r) for r in good] or ['<tr><td>No qualifying reels today.</td></tr>']
    parts.append("</table>")
    if cfg.get("include_flagged_section") and flagged:
        parts.append('<h3 style="font-family:Arial;color:#b00">Flagged / low-confidence</h3><table width="100%">')
        parts += [row_html(r) for r in flagged]
        parts.append("</table>")
    return "\n".join(parts)


def send_email(subject, body):
    user = os.environ["GMAIL_USER"]
    to = [a.strip() for a in os.environ.get("DIGEST_TO", user).split(",") if a.strip()]
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, user, ", ".join(to)
    msg.attach(MIMEText(body, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(user, os.environ["GMAIL_APP_PASSWORD"])
        s.sendmail(user, to, msg.as_string())


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", metavar="SAMPLE_JSON", help="use local JSON instead of Apify; don't email")
    args = ap.parse_args()
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())

    items = json.loads(Path(args.dry_run).read_text()) if args.dry_run else fetch_reels(cfg)
    seen = set() if args.dry_run else load_seen()
    posts = prefilter(items, cfg, seen)
    print(f"Fetched {len(items)} items, {len(posts)} new after dedupe/freshness")

    if posts:
        screened = ai_screen(posts, cfg) if os.environ.get("GEMINI_API_KEY") else heuristic_screen(posts)
    else:
        screened = {}
    good, flagged = merge(posts, screened, cfg)
    print(f"Likely genuine: {len(good)}, flagged: {len(flagged)}")

    body = build_html(good, flagged, cfg)
    subject = f'{cfg["email"]["subject_prefix"]}: {len(good)} new'
    if args.dry_run:
        (ROOT / "digest.html").write_text(body, encoding="utf-8")
        print("Dry run: wrote digest.html")
        return
    if good or flagged:
        send_email(subject, body)
        print("Email sent")
    else:
        print("Nothing new; no email sent")
    save_seen(seen | {p["key"] for p in posts})


if __name__ == "__main__":
    main()
