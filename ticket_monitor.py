#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║           Noah Kahan Ticket Monitor — ticket_monitor.py      ║
║  Checks every 5 min for resale tickets via Ticketmaster API  ║
║  and page scraping. Alerts via Discord and/or SMS (free).    ║
╚══════════════════════════════════════════════════════════════╝

Environment variables to set (via GitHub Secrets):
  TM_API_KEY          — Ticketmaster Developer API key (required)
  DISCORD_WEBHOOK_URL — Discord webhook URL (recommended)
  GMAIL_USER          — Your Gmail address (optional, for SMS)
  GMAIL_APP_PASSWORD  — Gmail App Password (optional, for SMS)
  PHONE_CARRIER_EMAIL — e.g., 2155551234@vtext.com (optional, for SMS)
"""

import os
import re
import json
import smtplib
import requests
from email.mime.text import MIMEText
from datetime import datetime, timezone

# ─── Configuration ────────────────────────────────────────────────────────────

TM_API_KEY          = os.environ.get("TM_API_KEY", "")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
GMAIL_USER          = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASSWORD  = os.environ.get("GMAIL_APP_PASSWORD", "")
PHONE_CARRIER_EMAIL = os.environ.get("PHONE_CARRIER_EMAIL", "")

# ─── Event Details ────────────────────────────────────────────────────────────

EVENT_ID   = "0200644110F3DABB"
EVENT_NAME = "Noah Kahan — The Great Divide Tour"
EVENT_INFO = "Philadelphia, PA · June 26, 2026"
EVENT_URL  = ("https://www.ticketmaster.com/noah-kahan-the-great-divide-tour-"
              "philadelphia-pennsylvania-06-26-2026/event/0200644110F3DABB")

# ─── Method 1: Ticketmaster Discovery API ─────────────────────────────────────

def check_via_api() -> tuple[bool, str]:
    if not TM_API_KEY:
        print("[API] No TM_API_KEY set — skipping.")
        return False, "no_key"

    # Search by keyword + date (more reliable than direct event ID lookup)
    url = "https://app.ticketmaster.com/discovery/v2/events.json"
    params = {
        "apikey":        TM_API_KEY,
        "keyword":       "Noah Kahan",
        "city":          "Philadelphia",
        "stateCode":     "PA",
        "startDateTime": "2026-06-26T00:00:00Z",
        "endDateTime":   "2026-06-27T00:00:00Z",
        "size":          5,
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()

        events = data.get("_embedded", {}).get("events", [])
        if not events:
            print("[API] No events found in search — no tickets available.")
            return False, "no_events_found"

        for event in events:
            name = event.get("name", "")
            if "noah kahan" in name.lower() or "great divide" in name.lower():
                status = event.get("dates", {}).get("status", {}).get("code", "unknown")
                prices = event.get("priceRanges", [])

                price_str = ""
                if prices:
                    lo = prices[0].get("min", "?")
                    hi = prices[0].get("max", "?")
                    price_str = f"${lo}–${hi}"

                detail = f"status={status}" + (f" | prices={price_str}" if price_str else "")
                print(f"[API] {name} | {detail}")

                available = (status == "onsale")
                return available, detail

        print(f"[API] Event not matched in {len(events)} result(s).")
        return False, "not_matched"

    except Exception as e:
        print(f"[API Error] {e}")
        return False, f"error: {e}"
      
# ─── Method 2: Ticketmaster Page Scrape ───────────────────────────────────────

def check_via_page() -> tuple[bool, str]:
    """
    Fetches the Ticketmaster event page and looks for resale ticket indicators.
    This catches resale tickets that may appear even when the API shows 'offsale'.
    Returns (tickets_available, tag_string)
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control":   "no-cache",
        "Pragma":          "no-cache",
    }
    try:
        r = requests.get(EVENT_URL, headers=headers, timeout=20)

        # Cloudflare may block the request
        if r.status_code in (403, 429):
            print(f"[Page] Blocked ({r.status_code}) — relying on API check only.")
            return False, "blocked"

        text_lower = r.text.lower()

        # Positive signals: ticket purchase is possible
        buy_phrases = [
            "get tickets", "buy tickets", "select tickets",
            "find tickets", "add to cart",
        ]
        resale_phrases = ["resale", "fan-to-fan", "verified resale"]

        has_buy    = any(p in text_lower for p in buy_phrases)
        has_resale = any(p in text_lower for p in resale_phrases)

        # Negative signals: definitely no tickets
        unavail_phrases = [
            "sold out",
            "no tickets available",
            "not currently available",
            "tickets unavailable",
        ]
        is_unavailable = any(p in text_lower for p in unavail_phrases)

        # Deep-check: parse the __NEXT_DATA__ embedded JSON (Next.js site)
        nd_match = re.search(
            r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
            r.text, re.DOTALL
        )
        if nd_match:
            try:
                nd_lower = nd_match.group(1).lower()
                if "resale" in nd_lower:
                    has_resale = True
                if "onsale" in nd_lower and not is_unavailable:
                    has_buy = True
            except Exception:
                pass

        available = (has_buy or has_resale) and not is_unavailable

        if has_resale:
            tag = "RESALE tickets detected"
        elif has_buy:
            tag = "buy button present"
        else:
            tag = "offsale/sold-out"

        detail = f"tag={tag} | sold_out={is_unavailable}"
        print(f"[Page] {detail}")
        return available, tag

    except Exception as e:
        print(f"[Page Error] {e}")
        return False, f"error: {e}"


# ─── Alerting ─────────────────────────────────────────────────────────────────

def send_discord(message: str):
    """Send a rich embed alert to your Discord channel via webhook."""
    if not DISCORD_WEBHOOK_URL:
        print("[Discord] No webhook URL set — skipping.")
        return
    try:
        payload = {
            "username": "🎫 Ticket Bot",
            "embeds": [{
                "title":       "🚨  TICKETS AVAILABLE — ACT NOW!",
                "description": message,
                "color":       0x00C853,   # green
                "url":         EVENT_URL,
                "footer":      {"text": "Click the title above to open Ticketmaster"},
                "timestamp":   datetime.now(timezone.utc).isoformat(),
            }]
        }
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        resp.raise_for_status()
        print("[Discord] Alert sent ✓")
    except Exception as e:
        print(f"[Discord Error] {e}")


def send_sms(message: str):
    """
    Send an SMS by emailing your carrier's email-to-SMS gateway via Gmail.
    Requires GMAIL_USER, GMAIL_APP_PASSWORD, and PHONE_CARRIER_EMAIL to be set.

    Common carrier gateways (replace 10digitnumber with your number):
      Verizon  → 10digitnumber@vtext.com
      AT&T     → 10digitnumber@txt.att.net
      T-Mobile → 10digitnumber@tmomail.net
      Cricket  → 10digitnumber@mms.cricketwireless.net
      Boost    → 10digitnumber@sms.myboostmobile.com
    """
    if not (GMAIL_USER and GMAIL_APP_PASSWORD and PHONE_CARRIER_EMAIL):
        print("[SMS] Missing credentials — skipping SMS.")
        return
    try:
        body = f"{message}\n\nBuy here:\n{EVENT_URL}"
        email = MIMEText(body)
        email["Subject"] = "TICKET ALERT"
        email["From"]    = GMAIL_USER
        email["To"]      = PHONE_CARRIER_EMAIL

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_USER, PHONE_CARRIER_EMAIL, email.as_string())
        print("[SMS] Alert sent ✓")
    except Exception as e:
        print(f"[SMS Error] {e}")


def fire_alerts(detail: str):
    """Send all configured alerts."""
    message = f"**{EVENT_NAME}**\n{EVENT_INFO}\n\n{detail}"
    send_discord(message)
    send_sms(f"{EVENT_NAME} | {EVENT_INFO} | {detail}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'='*56}")
    print(f"  Ticket check @ {now}")
    print(f"  {EVENT_NAME}")
    print(f"  {EVENT_INFO}")
    print(f"{'='*56}")

    tickets_found = False
    details       = []

    # --- Check 1: Discovery API ---
    api_found, api_detail = check_via_api()
    if api_found:
        tickets_found = True
        details.append(f"API → {api_detail}")

    # --- Check 2: Page scrape (catches resale even when API says offsale) ---
    page_found, page_detail = check_via_page()
    if page_found:
        tickets_found = True
        details.append(f"Page → {page_detail}")

    # --- Result ---
    if tickets_found:
        detail_str = "\n".join(details) if details else "Tickets detected"
        print(f"\n🚨  TICKETS FOUND! Firing alerts...")
        fire_alerts(detail_str)
        print("\n⚠️  Once you've purchased your ticket, disable the GitHub Actions")
        print("   workflow so you stop receiving alerts.")
    else:
        print(f"\n✅  No tickets yet. Workflow will check again in ~5 minutes.")


if __name__ == "__main__":
    main()
