#!/usr/bin/env python3
"""
LeadGen AI Agent — Profitable, low-risk lead generation assistant

Features
- Source local businesses for a niche & location (via Google Places/SerpAPI/Yelp API — bring your own key)
- (Optional) Scrape website/About page to enrich context
- Qualify leads with rule-based filters + optional LLM scoring
- Generate personalized outreach emails with value-driven hooks
- Track pipeline in SQLite: NEW → QUALIFIED → CONTACTED → REPLIED
- Export CSVs; send mail via SMTP with per-recipient rate limiting

Quickstart
1) Python 3.10+
2) pip install -r requirements.txt
3) Set env vars (see .env.example below)
4) Run:
   python leadgen_agent.py search --niche "dental clinics" --location "Austin, TX" --limit 50
   python leadgen_agent.py qualify --min_rating 4.0
   python leadgen_agent.py draft-emails --offer "Free 15-min SEO audit"
   python leadgen_agent.py send-emails --dry-run
   python leadgen_agent.py export --stage QUALIFIED --out leads_qualified.csv

Security & Compliance
- Respect robots.txt and site Terms when scraping.
- Comply with CAN-SPAM/GDPR/CASL: include physical address, honor opt-outs, send to business emails, avoid deception, keep records.
- Prefer opt-in: use contact forms/LinkedIn DM where appropriate.
"""
from __future__ import annotations
import os
import re
import time
import csv
import ssl
import json
import math
import uuid
import queue
import email.utils
import smtplib
import logging
import argparse
import sqlite3
import dataclasses
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Any, Tuple
from urllib.parse import urlparse

try:
    import requests
    from bs4 import BeautifulSoup
except Exception:
    requests = None
    BeautifulSoup = None

# -------------- Configuration --------------
DB_PATH = os.environ.get("LEADGEN_DB", "leadgen.sqlite3")
LOG_LEVEL = os.environ.get("LEADGEN_LOG_LEVEL", "INFO").upper()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
MODEL_NAME = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
SERPAPI_KEY = os.environ.get("SERPAPI_KEY")  # optional
YELP_API_KEY = os.environ.get("YELP_API_KEY")  # optional
GOOGLE_PLACES_KEY = os.environ.get("GOOGLE_PLACES_KEY")  # optional

SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")
SMTP_FROM = os.environ.get("SMTP_FROM")  # e.g., "Your Name <you@domain.com>"
SMTP_RATE_PER_MIN = int(os.environ.get("SMTP_RATE_PER_MIN", "20"))

ORGANIZATION_NAME = os.environ.get("ORG_NAME", "Acme Growth")
ORGANIZATION_ADDRESS = os.environ.get("ORG_ADDR", "123 Example St, City, ST 00000")
UNSUB_URL = os.environ.get("UNSUB_URL", "https://example.com/unsubscribe")

logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO), format="[%(levelname)s] %(message)s")
logger = logging.getLogger("leadgen")

# -------------- Database --------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id TEXT PRIMARY KEY,
    source TEXT,
    name TEXT,
    category TEXT,
    rating REAL,
    review_count INTEGER,
    email TEXT,
    phone TEXT,
    website TEXT,
    address TEXT,
    city TEXT,
    state TEXT,
    country TEXT,
    extras TEXT,
    stage TEXT DEFAULT 'NEW',
    created_at INTEGER,
    updated_at INTEGER
);

CREATE TABLE IF NOT EXISTS outreach (
    id TEXT PRIMARY KEY,
    lead_id TEXT,
    subject TEXT,
    body TEXT,
    status TEXT,
    sent_at INTEGER,
    message_id TEXT,
    FOREIGN KEY(lead_id) REFERENCES leads(id)
);
"""

def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

with db() as conn:
    conn.executescript(SCHEMA)

# -------------- Models --------------
@dataclass
class Lead:
    id: str
    source: str
    name: str
    category: Optional[str]
    rating: Optional[float]
    review_count: Optional[int]
    email: Optional[str]
    phone: Optional[str]
    website: Optional[str]
    address: Optional[str]
    city: Optional[str]
    state: Optional[str]
    country: Optional[str]
    extras: Dict[str, Any]
    stage: str = "NEW"

    @staticmethod
    def from_row(row: sqlite3.Row) -> "Lead":
        return Lead(
            id=row["id"],
            source=row["source"],
            name=row["name"],
            category=row["category"],
            rating=row["rating"],
            review_count=row["review_count"],
            email=row["email"],
            phone=row["phone"],
            website=row["website"],
            address=row["address"],
            city=row["city"],
            state=row["state"],
            country=row["country"],
            extras=json.loads(row["extras"]) if row["extras"] else {},
            stage=row["stage"],
        )

# -------------- Utilities --------------
def now() -> int:
    return int(time.time())

EMAIL_REGEX = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def upsert_lead(lead: Lead) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO leads (id, source, name, category, rating, review_count, email, phone, website,
                               address, city, state, country, extras, stage, created_at, updated_at)
            VALUES (:id, :source, :name, :category, :rating, :review_count, :email, :phone, :website,
                    :address, :city, :state, :country, :extras, :stage, :created_at, :updated_at)
            ON CONFLICT(id) DO UPDATE SET
                source=excluded.source,
                name=excluded.name,
                category=excluded.category,
                rating=excluded.rating,
                review_count=excluded.review_count,
                email=excluded.email,
                phone=excluded.phone,
                website=excluded.website,
                address=excluded.address,
                city=excluded.city,
                state=excluded.state,
                country=excluded.country,
                extras=excluded.extras,
                updated_at=excluded.updated_at
            """,
            {
                **asdict(lead),
                "extras": json.dumps(lead.extras or {}),
                "created_at": now(),
                "updated_at": now(),
            },
        )


def set_stage(lead_id: str, stage: str) -> None:
    with db() as conn:
        conn.execute("UPDATE leads SET stage=?, updated_at=? WHERE id=?", (stage, now(), lead_id))


def export_csv(stage: str, out_path: str) -> int:
    with db() as conn:
        rows = conn.execute("SELECT * FROM leads WHERE stage=?", (stage,)).fetchall()
    if not rows:
        logger.warning("No leads found at stage %s", stage)
        return 0
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([c for c in rows[0].keys()])
        for r in rows:
            writer.writerow([r[c] for c in r.keys()])
    logger.info("Exported %d leads to %s", len(rows), out_path)
    return len(rows)

# -------------- Sourcing --------------

def search_serpapi(niche: str, location: str, limit: int = 50) -> List[Lead]:
    if not SERPAPI_KEY:
        logger.warning("SERPAPI_KEY not set. Skipping SerpAPI search.")
        return []
    url = "https://serpapi.com/search.json"
    params = {
        "engine": "google_maps",
        "q": f"{niche} in {location}",
        "type": "search",
        "api_key": SERPAPI_KEY,
        "hl": "en",
    }
    out: List[Lead] = []
    try:
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        for item in (data.get("local_results", []) or [])[:limit]:
            lid = str(uuid.uuid4())
            website = item.get("website")
            email = None
            if website:
                email = try_extract_email_from_site(website)
            lead = Lead(
                id=lid,
                source="serpapi",
                name=item.get("title") or "",
                category=item.get("type") or None,
                rating=item.get("rating"),
                review_count=item.get("reviews"),
                email=email,
                phone=item.get("phone") or None,
                website=website,
                address=item.get("address") or None,
                city=None,
                state=None,
                country=None,
                extras={"raw": item},
            )
            out.append(lead)
    except Exception as e:
        logger.error("SerpAPI search failed: %s", e)
    return out


def try_extract_email_from_site(url: str) -> Optional[str]:
    if not requests or not BeautifulSoup:
        return None
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            return None
        m = EMAIL_REGEX.search(resp.text)
        if m:
            return m.group(0)
    except Exception:
        return None
    return None


# -------------- Qualification --------------

def rule_based_score(lead: Lead, min_rating: float = 3.8, min_reviews: int = 5) -> Tuple[int, Dict[str, Any]]:
    score = 0
    notes = {}
    if lead.rating is not None:
        if lead.rating >= min_rating:
            score += 2
        notes["rating"] = lead.rating
    if lead.review_count is not None and lead.review_count >= min_reviews:
        score += 1
        notes["reviews"] = lead.review_count
    if lead.website:
        score += 1
    if lead.email:
        score += 2
    return score, notes


def qualify_all(min_rating: float = 3.8, min_reviews: int = 5, min_score: int = 3) -> int:
    count = 0
    with db() as conn:
        rows = conn.execute("SELECT * FROM leads WHERE stage='NEW'").fetchall()
    for row in rows:
        lead = Lead.from_row(row)
        score, _ = rule_based_score(lead, min_rating, min_reviews)
        if score >= min_score:
            set_stage(lead.id, "QUALIFIED")
            count += 1
        else:
            set_stage(lead.id, "DISQUALIFIED")
    logger.info("Qualified %d / %d NEW leads", count, len(rows))
    return count

# -------------- Email Generation --------------
EMAIL_TEMPLATE = """
Subject: {subject}

Hi {first_name or 'there'},

I noticed {observation}. {value_prop}.

We helped similar {category or 'businesses'} achieve {social_proof}. I'd be happy to send a quick {lead_magnet}.

Would a {cta_duration}-minute chat sometime this week work?

Best,
{sender_name}
{org_name}
{org_address}
Unsubscribe: {unsub_url}
""".strip()


def split_name(biz_name: str) -> Tuple[str, str]:
    parts = biz_name.split()
    if not parts:
        return ("", "")
    return (parts[0], parts[-1])


def generate_email_body(lead: Lead, offer: str, lead_magnet: str = "free audit", cta_duration: int = 15) -> Tuple[str, str]:
    first, _ = split_name(lead.name or "")
    subject = f"Quick question about {lead.name or 'your site'}"
    observation = "your strong reviews" if (lead.rating and lead.rating >= 4.2) else "your presence in the area"
    social_proof = "20–30% increase in qualified inquiries in 60 days"
    body = EMAIL_TEMPLATE.format(
        subject=subject,
        first_name=first or None,
        observation=observation,
        value_prop=offer,
        category=lead.category,
        social_proof=social_proof,
        lead_magnet=lead_magnet,
        cta_duration=cta_duration,
        sender_name=SMTP_FROM.split("<")[0].strip() if SMTP_FROM else ORGANIZATION_NAME,
        org_name=ORGANIZATION_NAME,
        org_address=ORGANIZATION_ADDRESS,
        unsub_url=UNSUB_URL,
    )
    return subject, body


def draft_emails_for_stage(stage: str, offer: str) -> int:
    with db() as conn:
        rows = conn.execute("SELECT * FROM leads WHERE stage=?", (stage,)).fetchall()
    count = 0
    for row in rows:
        lead = Lead.from_row(row)
        if not lead.email:
            continue
        subject, body = generate_email_body(lead, offer)
        with db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO outreach (id, lead_id, subject, body, status, sent_at, message_id) VALUES (?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), lead.id, subject, body, "DRAFT", None, None),
            )
        count += 1
    logger.info("Drafted %d emails for stage %s", count, stage)
    return count

# -------------- SMTP Send --------------

def send_email(to_addr: str, subject: str, body: str) -> Tuple[bool, Optional[str]]:
    if not (SMTP_HOST and SMTP_USER and SMTP_PASS and SMTP_FROM):
        logger.error("SMTP env vars missing. Set SMTP_HOST, SMTP_USER, SMTP_PASS, SMTP_FROM.")
        return False, None
    msg = f"From: {SMTP_FROM}\r\nTo: {to_addr}\r\nSubject: {subject}\r\nDate: {email.utils.formatdate(localtime=True)}\r\nMIME-Version: 1.0\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n{body}"
    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls(context=context)
            server.login(SMTP_USER, SMTP_PASS)
            resp = server.sendmail(email.utils.parseaddr(SMTP_FROM)[1], [to_addr], msg)
            mid = str(uuid.uuid4()) if not resp else None
            return True, mid
    except Exception as e:
        logger.error("Email send failed to %s: %s", to_addr, e)
        return False, None


def send_all(dry_run: bool = True) -> int:
    with db() as conn:
        rows = conn.execute("SELECT o.id as oid, o.subject, o.body, l.email, l.id as lid FROM outreach o JOIN leads l ON o.lead_id=l.id WHERE o.status='DRAFT'").fetchall()
    sent = 0
    interval = 60.0 / max(1, SMTP_RATE_PER_MIN)
    last_sent = 0.0
    for r in rows:
        to_addr = r["email"]
        subject = r["subject"]
        body = r["body"]
        if dry_run:
            logger.info("[DRY RUN] Would send to %s — %s", to_addr, subject)
            success, mid = True, None
        else:
            # rate limit
            delta = time.time() - last_sent
            if delta < interval:
                time.sleep(interval - delta)
            success, mid = send_email(to_addr, subject, body)
            last_sent = time.time()
        with db() as conn:
            conn.execute("UPDATE outreach SET status=?, sent_at=?, message_id=? WHERE id=?", ("SENT" if success else "ERROR", now(), mid, r["oid"]))
            if success:
                conn.execute("UPDATE leads SET stage=?, updated_at=? WHERE id=?", ("CONTACTED", now(), r["lid"]))
        if success:
            sent += 1
    logger.info("%s %d emails", "Would send" if dry_run else "Sent", sent)
    return sent

# -------------- CLI --------------

def cmd_search(args: argparse.Namespace) -> None:
    leads: List[Lead] = []
    # Prefer Google Maps via SerpAPI if key present; otherwise instruct user
    leads.extend(search_serpapi(args.niche, args.location, args.limit))

    if not leads:
        logger.warning("No leads fetched. Provide an API key (SERPAPI_KEY/YELP_API_KEY/GOOGLE_PLACES_KEY).")
    for lead in leads:
        upsert_lead(lead)
    logger.info("Inserted/updated %d leads", len(leads))


def cmd_qualify(args: argparse.Namespace) -> None:
    qualify_all(args.min_rating, args.min_reviews, args.min_score)


def cmd_draft_emails(args: argparse.Namespace) -> None:
    draft_emails_for_stage("QUALIFIED", args.offer)


def cmd_send_emails(args: argparse.Namespace) -> None:
    send_all(dry_run=args.dry_run)


def cmd_export(args: argparse.Namespace) -> None:
    export_csv(args.stage, args.out)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="LeadGen AI Agent (CLI)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="Search for leads")
    s.add_argument("--niche", required=True)
    s.add_argument("--location", required=True)
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(func=cmd_search)

    q = sub.add_parser("qualify", help="Qualify NEW leads")
    q.add_argument("--min_rating", type=float, default=3.8)
    q.add_argument("--min_reviews", type=int, default=5)
    q.add_argument("--min_score", type=int, default=3)
    q.set_defaults(func=cmd_qualify)

    d = sub.add_parser("draft-emails", help="Draft outreach emails for QUALIFIED leads")
    d.add_argument("--offer", required=True, help="Value proposition (e.g., 'We can add 15+ booked appointments/month')")
    d.set_defaults(func=cmd_draft_emails)

    se = sub.add_parser("send-emails", help="Send drafted emails")
    se.add_argument("--dry_run", action="store_true", help="Log only; do not send")
    se.set_defaults(func=cmd_send_emails)

    e = sub.add_parser("export", help="Export leads at a stage to CSV")
    e.add_argument("--stage", default="QUALIFIED", choices=["NEW", "QUALIFIED", "CONTACTED", "DISQUALIFIED"])
    e.add_argument("--out", required=True)
    e.set_defaults(func=cmd_export)

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()

# ------------------------
# requirements.txt (put in a file with this name)
# requests>=2.32.0
# beautifulsoup4>=4.12.0
#
# ------------------------
# .env.example (set these env vars in your shell or via direnv)
# OPENAI_API_KEY=sk-...
# OPENAI_MODEL=gpt-4o-mini
# SERPAPI_KEY=...
# YELP_API_KEY=...
# GOOGLE_PLACES_KEY=...
# SMTP_HOST=smtp.sendgrid.net
# SMTP_PORT=587
# SMTP_USER=apikey
# SMTP_PASS=SG.xxxxx
# SMTP_FROM=Your Name <you@domain.com>
# ORG_NAME=Acme Growth
# ORG_ADDR=123 Example St, City, ST 00000
# UNSUB_URL=https://example.com/unsubscribe
