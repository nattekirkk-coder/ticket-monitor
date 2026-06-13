#!/usr/bin/env python3
"""
Ticketmaster Ticket Monitor — multi-show edition
Monitors multiple shows simultaneously and alerts via Discord / SMS.
"""

import os
import smtplib
import requests
from email.mime.text import MIMEText
from datetime import datetime, timezone

# ─── Credentials (set these as GitHub Secrets) ────────────────────────────────

TM_API_KEY          = os.environ.get("TM_API_KEY", "")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
GMAIL_USER          = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASSWORD  = os.environ.get("GMAIL_APP_PASSWORD", "")
PHONE_CARRIER_EMAIL = os.environ.get("PHONE_CARRIER_EMAIL", "")

# ─── Shows to Monitor ─────────────────────────────────────────────────────────
#
#  To add a show, copy one of the blocks below, uncomment it, and fill in:
#    label      → friendly name shown in your alert
#    keyword    → artist name to search for
#    city       → city name exactly as on Ticketmaster
#    state      → 2-letter state code
#    date_start → show date at midnight UTC  (YYYY-MM-DDT00:00:00Z)
#    date_end   → show date end of day UTC   (YYYY-MM-DDT23:59:59Z)
#    url        → paste the full Ticketmaster URL for that show
#
#  To find a NYC show URL: go to ticketmaster.com, search "Noah Kahan",
#  click the show you want, and copy the URL from your browser address bar.

EVENTS = [
    {
        "label":      "Philadelphia · June 26",
        "keyword":    "Noah Kahan",
        "city":       "Philadelphia",
        "state":      "PA",
        "date_start": "2026-06-26T00:00:00Z",
        "date_end":   "2026-06-27T00:00:00Z",
        "url":        "https://www.ticketmaster.com/noah-kahan-the-great-divide-tour-"
                      "philadelphia-pennsylvania-06-26-2026/event/0200644110F3DABB",
    },

    # ── Paste additional shows below this line ────────────────────────────────

     {
         "label":      "New York · July 18",
         "keyword":    "Noah Kahan",
         "date_start": "2026-07-18T00:00:00Z",
         "date_end":   "2026-07-19T23:59:59Z",
         "url":        "https://www.ticketmaster.com/noah-kahan-the-great-divide-tour-queens-new-york-07-18-2026/event/1D00644195271C17",
     },

     {
         "label":      "New York · July 19",
         "keyword":    "Noah Kahan",
         "date_start": "2026-07-19T00:00:00Z",
         "date_end":   "2026-07-20T23:59:59Z",
         "url":        "https://www.ticketmaster.com/noah-kahan-the-great-divide-tour-queens-new-york-07-19-2026/event/1D006446F9AB7790",
     },

   {
         "label":      "Washington · July 22",
         "keyword":    "Noah Kahan",
         "city":       "Washington",
         "state":      "DC",
         "date_start": "2026-07-22T00:00:00Z",
         "date_end":   "2026-07-23T23:59:59Z",
         "url":        "https://www.ticketmaster.com/noah-kahan-the-great-divide-tour-washington-district-of-columbia-07-22-2026/event/15006441D117A0B6",
     },
]

# ─── Ticket Checking ──────────────────────────────────────────────────────────

def check_event(event: dict) -> tuple[bool, str]:
    """Check one show for ticket availability via the Ticketmaster Discovery API."""
    if not TM_API_KEY:
        print("  [API] No TM_API_KEY set — add it as a GitHub Secret.")
        return False, "no_key"

    try:
        r = requests.get(
            "https://app.ticketmaster.com/discovery/v2/events.json",
            params = {
    "apikey":        TM_API_KEY,
    "keyword":       event["keyword"],
    "startDateTime": event["date_start"],
    "endDateTime":   event["date_end"],
    "size":          5,
}
# Only add location filters if the event specifies them
if event.get("city"):
    params["city"] = event["city"]
if event.get("state"):
    params["stateCode"] = event["state"]
            timeout=15,
        )
        r.raise_for_status()
        results = r.json().get("_embedded", {}).get("events", [])

        if not results:
            print("  [API] No events returned for this search.")
            return False, "not_found"

        for e in results:
            name = e.get("name", "")
            if event["keyword"].lower() in name.lower():
                status = e.get("dates", {}).get("status", {}).get("code", "unknown")
                prices = e.get("priceRanges", [])

                # ── THE FIX: only trigger on "onsale" ────────────────────────
                # Ticketmaster keeps old price ranges even after a show sells out,
                # so checking len(prices) > 0 causes constant false positives.
                # The real signal is the status flipping back to "onsale" when
                # resale tickets become available.
                available = (status == "onsale") and (len(prices) > 0)
                # ─────────────────────────────────────────────────────────────

                detail = f"status={status} | price_ranges={len(prices)}"
                if available and prices:
                    lo = prices[0].get("min", "?")
                    hi = prices[0].get("max", "?")
                    detail += f" | ${lo}–${hi}"

                print(f"  [API] {name} | {detail}")
                return available, detail

        print(f"  [API] Keyword not matched in {len(results)} result(s).")
        return False, "not_matched"

    except Exception as exc:
        print(f"  [API Error] {exc}")
        return False, f"error: {exc}"


# ─── Alerting ─────────────────────────────────────────────────────────────────

def send_discord(event: dict, detail: str):
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        requests.post(
            DISCORD_WEBHOOK_URL,
            json={
                "username": "🎫 Ticket Bot",
                "embeds": [{
                    "title":       "🚨  TICKETS AVAILABLE — ACT NOW!",
                    "description": f"**Noah Kahan — The Great Divide Tour**\n"
                                   f"{event['label']}\n\n{detail}",
                    "color":       0x00C853,
                    "url":         event["url"],
                    "footer":      {"text": "Click the title to open Ticketmaster"},
                    "timestamp":   datetime.now(timezone.utc).isoformat(),
                }],
            },
            timeout=10,
        ).raise_for_status()
        print("  [Discord] Alert sent ✓")
    except Exception as exc:
        print(f"  [Discord Error] {exc}")


def send_sms(event: dict, detail: str):
    if not (GMAIL_USER and GMAIL_APP_PASSWORD and PHONE_CARRIER_EMAIL):
        return
    try:
        body  = f"TICKET ALERT\nNoah Kahan — {event['label']}\n{detail}\n\n{event['url']}"
        email = MIMEText(body)
        email["Subject"] = "TICKET ALERT"
        email["From"]    = GMAIL_USER
        email["To"]      = PHONE_CARRIER_EMAIL
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            s.sendmail(GMAIL_USER, PHONE_CARRIER_EMAIL, email.as_string())
        print("  [SMS] Alert sent ✓")
    except Exception as exc:
        print(f"  [SMS Error] {exc}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'='*56}")
    print(f"  Ticket check @ {now}")
    print(f"  Monitoring {len(EVENTS)} show(s)")
    print(f"{'='*56}")

    for event in EVENTS:
        print(f"\n▶  {event['label']}")
        available, detail = check_event(event)

        if available:
            print("  🚨 TICKETS FOUND — firing alerts!")
            send_discord(event, detail)
            send_sms(event, detail)
        else:
            print(f"  ✅ No tickets yet.")

    print(f"\n{'='*56}\n")


if __name__ == "__main__":
    main()
