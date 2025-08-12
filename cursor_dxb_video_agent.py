#!/usr/bin/env python3
# =============================================================================
# Cursor-Optimized DXB Video Editing Lead Agent — single file, stdlib only
# Run: python cursor_dxb_video_agent.py
#
# CURSOR_RULES (for the AI editor):
# - Keep this project SINGLE FILE and STDLIB-ONLY (no external deps, no APIs, no SMTP).
# - Preserve the ability to run with: python cursor_dxb_video_agent.py
# - Only modify inside EDITABLE ZONES unless explicitly requested.
# - Keep function boundaries small & pure where possible.
# - When changing templates, preserve {placeholders}.
# - When adding features, prefer optional flags in CONFIG EDITABLE ZONE.
# =============================================================================

from __future__ import annotations
import csv, os, re, uuid, textwrap
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
from datetime import datetime

# =============================================================================
# REGION: EDITABLE ZONE — CONFIG
# (Cursor: safe place to modify)
# =============================================================================
CONFIG: Dict[str, object] = {
    # Core
    "CITY": "Dubai, UAE",
    "BRAND": "DXB Edits",
    # If empty, a smart default is used for the city.
    "OFFER_TEXT": "",
    # Filters (empty list => include all)
    "FILTER_NICHES": ["real estate", "restaurant", "gym", "clinic"],
    "FILTER_PLATFORMS": ["email", "whatsapp", "instagram", "linkedin"],
    # CSV toggle (leave False to use embedded leads below)
    "USE_CSV": False,
    "CSV_PATH": "leads.csv",
    # Extras
    "AUTO_GENERATE_LANDING": True,
    "RUN_SMOKE_TEST": False,  # set True to generate a tiny test pass file
    # Output directory
    "OUTPUT_DIR": "output",
}
# =============================================================================
# END EDITABLE ZONE — CONFIG
# =============================================================================


# =============================================================================
# REGION: EDITABLE ZONE — EMBEDDED LEADS
# (Cursor: safe place to modify)
# Columns: Name, Niche, Contact, Platform
# =============================================================================
EMBEDDED_LEADS: List[Dict[str, str]] = [
    {"Name": "Al Noor Realty",         "Niche": "real estate", "Contact": "info@alnoorrealty.ae",              "Platform": "email"},
    {"Name": "Emaar Agent Team",       "Niche": "real estate", "Contact": "https://linkedin.com/in/agentxyz",  "Platform": "linkedin"},
    {"Name": "Taste of Dubai",         "Niche": "restaurant",  "Contact": "@tasteofdubai",                     "Platform": "instagram"},
    {"Name": "Marina Fitness JLT",     "Niche": "gym",         "Contact": "+971501234567",                     "Platform": "whatsapp"},
    {"Name": "Glow Aesthetics Clinic", "Niche": "clinic",      "Contact": "hello@glowaesthetics.ae",           "Platform": "email"},
    {"Name": "Prime Motors Deira",     "Niche": "auto",        "Contact": "@primemotorsdxb",                   "Platform": "instagram"},
]
# =============================================================================
# END EDITABLE ZONE — EMBEDDED LEADS
# =============================================================================


# =============================================================================
# REGION: CONSTANTS & MODELS
# =============================================================================
OUTPUT_DIR = str(CONFIG.get("OUTPUT_DIR", "output"))

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"\+?\d[\d\s-]{6,}\d")
IG_RE    = re.compile(r"^@?[A-Za-z0-9_.]{2,30}$")

NICHE_WEIGHTS: Dict[str, int] = {
    "real estate": 3, "restaurant": 2, "gym": 2, "clinic": 3,
    "auto": 2, "salon": 2, "cafe": 2,
}
PLATFORM_WEIGHTS: Dict[str, int] = {
    "email": 3, "whatsapp": 3, "instagram": 2, "linkedin": 2,
}

@dataclass
class Lead:
    id: str
    name: str
    niche: str
    contact: str   # email, phone, handle, or URL
    platform: str  # email|whatsapp|instagram|linkedin
    score: int = 0
    notes: str = ""
# =============================================================================
# END REGION
# =============================================================================


# =============================================================================
# REGION: SMALL UTILS
# =============================================================================

def now_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")


def ensure_out_dir(path: str = OUTPUT_DIR) -> None:
    os.makedirs(path, exist_ok=True)


def offer_default(city: str) -> str:
    return (
        f"Short-form edits + captions in 48h, 3x hooks per video, and platform-native formats "
        f"(Reels/TikTok/Shorts) — tailored for {city}."
    )


def dedupe(leads: List[Lead]) -> List[Lead]:
    seen = set()
    out: List[Lead] = []
    for l in leads:
        key = (l.name.lower(), l.contact.lower(), l.platform.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(l)
    return out
# =============================================================================
# END REGION
# =============================================================================


# =============================================================================
# REGION: QUALIFICATION
# =============================================================================

def personalize_observation(niche: str, city: str) -> str:
    obs = {
        "real estate": f"Dubai property reels are exploding — agents winning use 4–7s hooks and subtitles",
        "restaurant":  f"Food reels in {city} perform best with overhead shots + on-screen prices",
        "gym":         f"Fitness content with timer overlays and form tips drives saves in {city}",
        "clinic":      f"Before/after edits with compliant captions convert for clinics in {city}",
        "auto":        f"Showroom walkarounds with spec popups perform well in {city}",
    }
    return obs.get(niche, f"Short-form clips with captions and on-screen text are outperforming in {city}")


def qualify(lead: Lead, city: str) -> Lead:
    score = 0
    notes: List[str] = []

    score += NICHE_WEIGHTS.get(lead.niche, 1)
    score += PLATFORM_WEIGHTS.get(lead.platform, 1)

    if lead.platform == "email" and EMAIL_RE.search(lead.contact):
        score += 2; notes.append("valid_email")
    if lead.platform == "whatsapp" and PHONE_RE.search(lead.contact):
        score += 2; notes.append("valid_phone")
    if lead.platform == "instagram" and IG_RE.search(lead.contact):
        score += 1; notes.append("handle_ok")
    if lead.platform == "linkedin" and "linkedin.com" in lead.contact:
        score += 1; notes.append("linkedin_url")

    if lead.niche in {"real estate", "restaurant", "clinic", "auto", "salon", "gym", "cafe"}:
        score += 1; notes.append("visual_niche")

    lead.score = score
    lead.notes = ",".join(notes)
    return lead
# =============================================================================
# END REGION
# =============================================================================


# =============================================================================
# REGION: MESSAGING TEMPLATES
# =============================================================================

def subject_lines(name: str, niche: str) -> List[str]:
    return [
        f"Fast video edits for {name}",
        f"Reels that book more {niche} clients",
        "2-day turnaround on high-retention edits",
        "Quick idea: 5 hooks for your next reel",
        "Dubai-ready content in 48h",
    ]


def email_template(lead: Lead, city: str, brand: str, offer_text: str) -> str:
    obs = personalize_observation(lead.niche, city)
    subs = subject_lines(lead.name, lead.niche)
    return textwrap.dedent(f"""
    Subject: {subs[0]}

    Hi {lead.name.split()[0] if lead.name else 'there'},

    {obs}. I help {lead.niche} brands turn raw footage into scroll-stopping Reels/Shorts.

    Offer: {offer_text}

    Idea starters for you:
    • 30-sec reel with 3 hooks
    • Captions + emoji callouts + auto-subtitles
    • 9:16, 1:1, 16:9 variants delivered together

    If I cut a 20s sample from your existing footage (free), would you review it?

    — {brand}
    """).strip()


def whatsapp_template(lead: Lead, city: str, brand: str, offer_text: str) -> str:
    obs = personalize_observation(lead.niche, city)
    return (
        f"Hey {lead.name.split()[0] if lead.name else ''}! {obs}. "
        f"I do fast video edits (Reels/TikTok/Shorts). {offer_text} — "
        f"Want a free 20s sample? — {brand}"
    ).strip()


def instagram_template(lead: Lead, city: str, brand: str, offer_text: str) -> str:
    return (
        f"Love your page, {lead.name}! I edit high-retention Reels for {lead.niche} in {city}. "
        f"{offer_text} If I cut a 20s sample from your footage (free), can I DM it here? — {brand}"
    )


def linkedin_template(lead: Lead, city: str, brand: str, offer_text: str) -> Tuple[str, str]:
    note = (
        f"Hi {lead.name}, I help {lead.niche} teams in {city} turn raw clips into Reels/Shorts. "
        f"{offer_text} Open to a free 20s sample? — {brand}"
    )
    followup = "Quick follow-up: happy to send 3 tailored hook ideas for your next video (free). Interested?"
    return note, followup
# =============================================================================
# END REGION
# =============================================================================


# =============================================================================
# REGION: IO — CSV & EXPORTS
# =============================================================================

def read_csv_leads(path: str) -> List[Lead]:
    leads: List[Lead] = []
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            name = (row.get("Name") or row.get("name") or "").strip()
            niche = (row.get("Niche") or row.get("niche") or "").strip().lower()
            contact = (row.get("Contact") or row.get("contact") or "").strip()
            platform = (row.get("Platform") or row.get("platform") or "").strip().lower()
            if not name or not contact or not platform:
                continue
            leads.append(Lead(str(uuid.uuid4()), name, niche, contact, platform))
    return dedupe(leads)


def embedded_leads() -> List[Lead]:
    leads: List[Lead] = []
    for row in EMBEDDED_LEADS:
        name = (row.get("Name") or "").strip()
        niche = (row.get("Niche") or "").strip().lower()
        contact = (row.get("Contact") or "").strip()
        platform = (row.get("Platform") or "").strip().lower()
        if not name or not contact or not platform:
            continue
        leads.append(Lead(str(uuid.uuid4()), name, niche, contact, platform))
    return dedupe(leads)


def export_scored_csv(leads: List[Lead], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ID", "Name", "Niche", "Contact", "Platform", "Score", "Notes"])
        for l in sorted(leads, key=lambda x: x.score, reverse=True):
            w.writerow([l.id, l.name, l.niche, l.contact, l.platform, l.score, l.notes])


def export_outreach_pack(leads: List[Lead], city: str, brand: str, offer_text: str) -> None:
    ensure_out_dir()
    master = []
    for l in sorted(leads, key=lambda x: x.score, reverse=True):
        if l.platform == "email":
            msg = email_template(l, city, brand, offer_text)
        elif l.platform == "whatsapp":
            msg = whatsapp_template(l, city, brand, offer_text)
        elif l.platform == "instagram":
            msg = instagram_template(l, city, brand, offer_text)
        elif l.platform == "linkedin":
            note, follow = linkedin_template(l, city, brand, offer_text)
            msg = f"Connection Note:\n{note}\n\nFollow-up:\n{follow}"
        else:
            continue

        slug = re.sub(r"[^a-z0-9]+", "-", (l.name or "lead").lower())[:40]
        lead_dir = os.path.join(OUTPUT_DIR, slug)
        os.makedirs(lead_dir, exist_ok=True)
        with open(os.path.join(lead_dir, f"{l.platform}.txt"), "w", encoding="utf-8") as f:
            f.write(msg + "\n")

        master.append({"name": l.name, "platform": l.platform, "contact": l.contact, "message": msg})

    with open(os.path.join(OUTPUT_DIR, "outreach_pack.txt"), "w", encoding="utf-8") as f:
        f.write(f"Generated: {now_str()}\nCity: {city}\nBrand: {brand}\n\n")
        for item in master:
            f.write("=" * 60 + "\n")
            f.write(f"{item['name']} — {item['platform']} — {item['contact']}\n\n")
            f.write(item["message"] + "\n\n")


def write_landing_html(brand: str, city: str, offer_text: str, path: str = "landing.html") -> None:
    html = f"""
<!doctype html>
<html lang="en"><head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{brand} — Video Editing in {city}</title>
<style>
  body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial; margin: 0; color: #0f172a; background:#f8fafc; }}
  .wrap {{ max-width: 880px; margin: 0 auto; padding: 32px; }}
  .card {{ background: white; border-radius: 16px; padding: 24px; box-shadow: 0 10px 30px rgba(2,6,23,0.08); margin: 18px 0; }}
  h1 {{ font-size: 32px; margin: 0 0 8px; }}
  h2 {{ font-size: 20px; margin: 0 0 12px; color:#334155; }}
  ul {{ line-height: 1.6; }}
  .btn {{ display:inline-block; padding:12px 16px; border-radius:12px; background:#0ea5e9; color:white; text-decoration:none; font-weight:600; }}
  .muted {{ color:#64748b; }}
</style>
</head><body>
<div class="wrap">
  <div class="card">
    <h1>{brand} — High-Retention Video Editing</h1>
    <p class="muted">Serving {city}</p>
    <p>{offer_text}</p>
    <a class="btn" href="#contact">Get a free 20s sample edit →</a>
  </div>

  <div class="card">
    <h2>Why us</h2>
    <ul>
      <li>48-hour turnaround</li>
      <li>Subtitles, emojis, sound design, and hook testing</li>
      <li>Formats for Reels, TikTok, YouTube Shorts</li>
      <li>Batch pricing for agencies and teams</li>
    </ul>
  </div>

  <div class="card">
    <h2>Packages</h2>
    <ul>
      <li><strong>Starter</strong>: 4 videos / mo — AED 1,600</li>
      <li><strong>Growth</strong>: 12 videos / mo — AED 4,200</li>
      <li><strong>Scale</strong>: 25 videos / mo — AED 8,000</li>
    </ul>
  </div>

  <div id="contact" class="card">
    <h2>Contact</h2>
    <p>Email: yourname@yourdomain.ae — WhatsApp: +971 50 000 0000</p>
  </div>
</div>
</body></html>
""".strip()
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
# =============================================================================
# END REGION
# =============================================================================


# =============================================================================
# REGION: ORCHESTRATION
# =============================================================================

def load_leads() -> List[Lead]:
    use_csv = bool(CONFIG.get("USE_CSV", False))
    csv_path = str(CONFIG.get("CSV_PATH", "leads.csv"))
    return read_csv_leads(csv_path) if (use_csv and os.path.exists(csv_path)) else embedded_leads()


def apply_filters(leads: List[Lead]) -> List[Lead]:
    niches = [s.lower() for s in (CONFIG.get("FILTER_NICHES") or [])]
    platforms = [s.lower() for s in (CONFIG.get("FILTER_PLATFORMS") or [])]
    out: List[Lead] = []
    for l in leads:
        if niches and l.niche not in niches:
            continue
        if platforms and l.platform not in platforms:
            continue
        out.append(l)
    return out


def run_agent() -> None:
    ensure_out_dir()
    city = str(CONFIG.get("CITY", "Dubai, UAE"))
    brand = str(CONFIG.get("BRAND", "DXB Edits"))
    offer_text = str(CONFIG.get("OFFER_TEXT") or offer_default(city))

    leads = load_leads()
    if not leads:
        print("No leads found. Add entries to EMBEDDED_LEADS or enable CSV.")
        return

    filtered = apply_filters(leads)
    if not filtered:
        print("No leads met your niche/platform filters. Adjust FILTER_* in CONFIG.")
        return

    qualified = [qualify(l, city) for l in filtered]
    export_scored_csv(qualified, os.path.join(OUTPUT_DIR, "leads_scored.csv"))
    export_outreach_pack(qualified, city, brand, offer_text)

    if bool(CONFIG.get("AUTO_GENERATE_LANDING", True)):
        write_landing_html(brand, city, offer_text)

    if bool(CONFIG.get("RUN_SMOKE_TEST", False)):
        with open(os.path.join(OUTPUT_DIR, "smoke_test.ok"), "w") as f:
            f.write(now_str() + " OK\n")

    print("✅ Done! Outputs:")
    print(f"  - {os.path.join(OUTPUT_DIR, 'leads_scored.csv')}")
    print(f"  - {os.path.join(OUTPUT_DIR, 'outreach_pack.txt')}")
    print(f"  - Per-lead messages under: {OUTPUT_DIR}/<lead-name>/<platform>.txt")
    if bool(CONFIG.get("AUTO_GENERATE_LANDING", True)):
        print("  - landing.html")
# =============================================================================
# END REGION
# =============================================================================


if __name__ == "__main__":
    run_agent()