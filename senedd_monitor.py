#!/usr/bin/env python3
"""
Senedd PRS Monitor — Daily updater
Fetches written questions about the private rented sector from the Welsh
Parliament (Senedd) and saves them to docs/senedd_data.json for the web
dashboard.
 
No external dependencies beyond the stdlib + requests + beautifulsoup4.
Install: pip install requests beautifulsoup4
"""
 
import json
import os
import re
import time
from datetime import date, datetime, timedelta
from collections import defaultdict
 
import requests
from bs4 import BeautifulSoup
 
# ── Configuration ──────────────────────────────────────────────────────────────
 
DATA_FILE = os.path.join("docs", "senedd_data.json")
 
# Power Automate "When an HTTP request is received" webhook URL.
# Set as a GitHub secret named SENEDD_EMAIL_WEBHOOK.
# If not set, email notifications are silently skipped.
EMAIL_WEBHOOK = os.environ.get("SENEDD_EMAIL_WEBHOOK", "")
 
# Public URL of the dashboard, included in the email footer
DASHBOARD_URL = "https://jhc220199.github.io/PA-Monitoring-Tools/senedd_index.html"
 
# First sitting of the 7th Senedd — do not fetch questions before this date
SENEDD_START = date(2026, 5, 12)
 
# How far back to search on each run (catches any missed questions)
LOOKBACK_DAYS = 21
 
BASE_URL   = "https://record.senedd.wales"
MEMBER_URL = "https://business.senedd.wales/mgUserInfo.aspx?UID={uid}"
 
KEYWORDS = [
    # Welsh-specific legislation & terms
    "renting homes wales",
    "renting homes (wales)",
    "occupation contract",
    "contract-holder",
    "rent smart wales",
    "section 173",
    "housing (wales) act",
    # Welsh Government housing bodies & initiatives
    "unnos",
    # General PRS terms
    "private rented sector",
    "renting in wales",
    "renting",
    "landlord",
    "local housing allowance",
    "energy performance certificate",
    "energy efficiency",
    "tenancy deposit",
    "no fault eviction",
    "no-fault eviction",
    "eviction notice",
    "rent arrears",
    "landlord licensing",
    "rent repayment",
    "housing benefit",
    "lha",
    "epc",
    "renters",
    # Building safety
    "cladding",
    "fire safety defects",
    "building safety",
    "remediation",
    # Planning & permitted development
    "permitted development",
    "article 4",
    "dwellinghouse",
    "dwelling house",
    # Property acquisition & housing policy
    "right to buy",
    "second homes",
    "council tax premium",
    "empty homes",
    "housing supply",
    "affordable housing",
    "leasehold",
    "freeholder",
    "ground rent",
    "service charge",
]
 
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
})
 
_party_cache: dict = {}
 
# ── Helpers ────────────────────────────────────────────────────────────────────
 
def clean(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()
 
def parse_dmy(s: str):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%d/%m/%Y").date()
    except ValueError:
        return None
 
def week_commencing(d: date) -> str:
    monday = d - timedelta(days=d.weekday())
    return monday.isoformat()
 
def buf_date(d: date, days: int) -> str:
    return (d + timedelta(days=days)).strftime("%d/%m/%Y")
 
# ── Data Fetching ──────────────────────────────────────────────────────────────
 
def get_party(uid: str) -> str:
    if not uid:
        return ""
    if uid in _party_cache:
        return _party_cache[uid]
    try:
        r = SESSION.get(MEMBER_URL.format(uid=uid), timeout=15, allow_redirects=True)
        soup = BeautifulSoup(r.text, "html.parser")
        el = soup.find("p", class_=lambda c: c and "party" in c)
        if el:
            party = clean(el.get_text())
            _party_cache[uid] = party
            return party
    except Exception:
        pass
    _party_cache[uid] = ""
    return ""
 
def parse_cards(html_items) -> list:
    results = []
    for html in html_items:
        try:
            if isinstance(html, str):
                soup = BeautifulSoup(html, "html.parser")
            else:
                soup = html
            card = soup.find("div") if soup.name != "div" else soup
 
            link = card.find("a", class_="detail") if card else None
            if not link:
                continue
            url_path = link.get("href", "").replace("..", "")
 
            title_el = card.find("span", class_="title") if card else None
            if not title_el:
                continue
            wq_match = re.search(r"(W[AQ]+\d+)", clean(title_el.get_text()))
            if not wq_match:
                continue
            wq_ref = wq_match.group(1)
 
            sub = clean(card.find("span", class_="subTitle").get_text()) if card.find("span", class_="subTitle") else ""
            tabled_m = re.search(r"Tabled on (\d{2}/\d{2}/\d{4})", sub)
            answer_m = re.search(r"for answer on (\d{2}/\d{2}/\d{4})", sub)
 
            ctx = card.find("div", class_="context") if card else None
            snippet = clean(ctx.get_text()) if ctx else ""
 
            member_name = member_area = member_uid = ""
            bar = card.find("div", class_="memberBar") if card else None
            if bar:
                n = bar.find("span", class_="name")
                a = bar.find("span", class_="area")
                u = bar.find("a", href=lambda h: h and "UID=" in h)
                if n: member_name = clean(n.get_text())
                if a: member_area = clean(a.get_text())
                if u:
                    um = re.search(r"UID=(\d+)", u.get("href", ""))
                    if um: member_uid = um.group(1)
 
            results.append({
                "wq_ref":      wq_ref,
                "url_path":    url_path,
                "tabled_str":  tabled_m.group(1) if tabled_m else "",
                "answer_str":  answer_m.group(1) if answer_m else "",
                "snippet":     snippet,
                "member_name": member_name,
                "member_area": member_area,
                "member_uid":  member_uid,
            })
        except Exception:
            continue
    return results
 
 
def search_keyword(keyword: str, date_from: date, date_to: date) -> list:
    results = []
    fetch_from = buf_date(date_from, -7)
    fetch_to   = buf_date(date_to,    7)
    page = 1
 
    while page <= 25:
        try:
            r = SESSION.get(
                f"{BASE_URL}/Search/SeeMore",
                params={
                    "Query":            keyword,
                    "MemberID":         "-1",
                    "Type":             "7",
                    "Start":            fetch_from,
                    "End":              fetch_to,
                    "MeetingType":      "-1",
                    "MotionType":       "-1",
                    "OrderPaperFilter": "False",
                    "Page":             page,
                    "Unselected":       "All",
                },
                headers={"X-Requested-With": "XMLHttpRequest"},
                timeout=20,
            )
            data = r.json()
            blocks = data.get("Results", [])
            if not blocks:
                break
            results.extend(parse_cards([BeautifulSoup(h, "html.parser") for h in blocks]))
            if not data.get("MoreToShow", False):
                break
            page += 1
            time.sleep(0.25)
        except Exception as e:
            print(f"    ⚠ Error page {page} for '{keyword}': {e}")
            break
 
    # Client-side strict date filter
    return [q for q in results if (
        parse_dmy(q["tabled_str"]) is not None and
        date_from <= parse_dmy(q["tabled_str"]) <= date_to
    )]
 
 
def fetch_full_question(url_path: str) -> dict:
    result = {"full_question": "", "answered_by": "", "answered_on": ""}
    try:
        r = SESSION.get(f"{BASE_URL}{url_path}", timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        wq_div = soup.find("div", class_="writtenQuestion")
        if wq_div:
            q_content = wq_div.find("div", class_="itemContent__content")
            if q_content:
                result["full_question"] = clean(q_content.get_text())
            answer_div = wq_div.find("div", class_="answer")
            if answer_div:
                keyline = answer_div.find("span", class_="keyline")
                if keyline:
                    kt = clean(keyline.get_text())
                    by_m = re.search(r"Answered by\s+(.+?)(?:\s*\|\s*Answered on|$)", kt)
                    on_m = re.search(r"Answered on\s+(\d{2}/\d{2}/\d{4})", kt)
                    if by_m: result["answered_by"] = by_m.group(1).strip()
                    if on_m: result["answered_on"] = on_m.group(1)
    except Exception:
        pass
    return result
 
# ── Email notification ─────────────────────────────────────────────────────────
 
PARTY_ABBREV = {
    "Labour": "Lab", "Welsh Labour": "Lab",
    "Welsh Conservatives": "Con", "Welsh Conservative Party": "Con", "Conservative": "Con",
    "Plaid Cymru": "PC", "Plaid Cymru Group": "PC",
    "Liberal Democrats": "LD", "Liberal Democrat": "LD", "Welsh Liberal Democrats": "LD",
    "Reform UK": "Ref", "Reform UK Group": "Ref",
    "Wales Green Party": "Green", "Green Party": "Green",
    "Independent": "Ind", "Non-affiliated": "Ind",
}
 
PARTY_STYLE = {
    "Lab":   ("#fee2e2", "#991b1b"),
    "Con":   ("#dbeafe", "#1e40af"),
    "LD":    ("#fef3c7", "#92400e"),
    "PC":    ("#99f6e4", "#134e4a"),
    "Ref":   ("#cffafe", "#0891b2"),
    "Green": ("#bbf7d0", "#14532d"),
    "Ind":   ("#f1f5f9", "#475569"),
}
 
def party_abbrev(party: str) -> str:
    if party in PARTY_ABBREV:
        return PARTY_ABBREV[party]
    for k, v in PARTY_ABBREV.items():
        if party.startswith(k) or k.startswith(party):
            return v
    return party or ""
 
def esc_html(s: str) -> str:
    return (str(s or "")
            .replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))
 
def build_email_html(new_qs: list) -> str:
    """Build the HTML body for the notification email."""
    n = len(new_qs)
    rows = ""
    for q in new_qs:
        abbr = party_abbrev(q.get("party", ""))
        bg, fg = PARTY_STYLE.get(abbr, ("#f1f5f9", "#475569"))
        badge = (
            f'<span style="display:inline-block;padding:2px 8px;border-radius:4px;'
            f'font-size:11px;font-weight:600;background:{bg};color:{fg};">{esc_html(abbr)}</span>'
        ) if abbr else ""
 
        q_text = q.get("full_question") or q.get("snippet") or ""
        q_url  = f"{BASE_URL}{q.get('url_path','')}"
 
        rows += f"""
        <tr>
          <td style="padding:12px 10px;border-bottom:1px solid #e2e8f0;vertical-align:top;">
            <div style="font-weight:600;color:#113B54;font-size:13px;">{esc_html(q.get('member_name',''))}</div>
            <div style="font-size:11px;color:#94a3b8;margin-top:2px;">{esc_html(q.get('member_area',''))}</div>
            <div style="margin-top:5px;">{badge}</div>
          </td>
          <td style="padding:12px 10px;border-bottom:1px solid #e2e8f0;vertical-align:top;">
            <div style="font-size:13px;color:#0F2636;line-height:1.5;">{esc_html(q_text)}</div>
            <a href="{esc_html(q_url)}"
               style="display:inline-block;margin-top:7px;font-size:12px;color:#E96C19;text-decoration:none;">
               View on Senedd website &rarr;</a>
            <div style="font-size:11px;color:#94a3b8;margin-top:4px;">{esc_html(q.get('wq_ref',''))}</div>
          </td>
          <td style="padding:12px 10px;border-bottom:1px solid #e2e8f0;vertical-align:top;
                     font-size:12px;color:#64748b;white-space:nowrap;">
            {esc_html(q.get('tabled_str','—'))}
          </td>
          <td style="padding:12px 10px;border-bottom:1px solid #e2e8f0;vertical-align:top;
                     font-size:12px;color:#64748b;white-space:nowrap;">
            {esc_html(q.get('answer_str','—'))}
          </td>
        </tr>"""
 
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#F6F7F8;
     font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;">
  <div style="max-width:900px;margin:0 auto;background:#FCFCFC;">
 
    <div style="background:#113B54;color:#FCFCFC;padding:20px 24px;">
      <div style="font-size:18px;font-weight:600;">Senedd PRS Written Questions Monitor</div>
      <div style="font-size:13px;color:rgba(252,252,252,0.65);margin-top:4px;">
        {n} new question{'s' if n != 1 else ''} identified &middot;
        {datetime.now().strftime('%d %B %Y')}
      </div>
    </div>
 
    <div style="padding:20px 24px;">
      <p style="font-size:14px;color:#0F2636;margin:0 0 16px;">
        The following written question{'s have' if n != 1 else ' has'} been tabled in the Senedd
        and may be relevant to the NRLA:
      </p>
 
      <table style="width:100%;border-collapse:collapse;">
        <thead>
          <tr style="background:#f8fafc;">
            <th style="padding:9px 10px;text-align:left;font-size:10.5px;font-weight:600;
                       text-transform:uppercase;letter-spacing:0.07em;color:#94a3b8;
                       border-bottom:1px solid #e2e8f0;">Member</th>
            <th style="padding:9px 10px;text-align:left;font-size:10.5px;font-weight:600;
                       text-transform:uppercase;letter-spacing:0.07em;color:#94a3b8;
                       border-bottom:1px solid #e2e8f0;">Question</th>
            <th style="padding:9px 10px;text-align:left;font-size:10.5px;font-weight:600;
                       text-transform:uppercase;letter-spacing:0.07em;color:#94a3b8;
                       border-bottom:1px solid #e2e8f0;">Tabled</th>
            <th style="padding:9px 10px;text-align:left;font-size:10.5px;font-weight:600;
                       text-transform:uppercase;letter-spacing:0.07em;color:#94a3b8;
                       border-bottom:1px solid #e2e8f0;">Due for answer</th>
          </tr>
        </thead>
        <tbody>{rows}
        </tbody>
      </table>
 
      <div style="margin-top:24px;padding-top:16px;border-top:1px solid #e2e8f0;">
        <a href="{DASHBOARD_URL}"
           style="display:inline-block;background:#E96C19;color:#FCFCFC;padding:9px 18px;
                  border-radius:6px;font-size:13px;font-weight:600;text-decoration:none;">
          View full monitor &rarr;</a>
      </div>
 
      <p style="font-size:11px;color:#94a3b8;margin-top:20px;line-height:1.5;">
        Automated notification from the Senedd PRS Written Questions Monitor.
        Questions are filtered by keyword for potential private rented sector relevance
        and may require review.
      </p>
    </div>
  </div>
</body></html>"""
 
 
def send_notification(new_qs: list) -> bool:
    """POST the new questions to the Power Automate webhook. Returns True on success."""
    if not EMAIL_WEBHOOK:
        print("  ℹ  No SENEDD_EMAIL_WEBHOOK set — skipping email notification.")
        return False
    if not new_qs:
        return False
 
    n = len(new_qs)
    subject = (f"Senedd Monitor: {n} new PRS written question"
               f"{'s' if n != 1 else ''} tabled")
 
    payload = {
        "subject":   subject,
        "body":      build_email_html(new_qs),
        "count":     n,
        "questions": [
            {
                "member":     q.get("member_name", ""),
                "party":      q.get("party", ""),
                "area":       q.get("member_area", ""),
                "question":   q.get("full_question") or q.get("snippet", ""),
                "url":        f"{BASE_URL}{q.get('url_path','')}",
                "wq_ref":     q.get("wq_ref", ""),
                "tabled":     q.get("tabled_str", ""),
                "due":        q.get("answer_str", ""),
            }
            for q in new_qs
        ],
    }
 
    try:
        r = requests.post(EMAIL_WEBHOOK, json=payload, timeout=30)
        if 200 <= r.status_code < 300:
            print(f"  ✉  Email notification sent for {n} question{'s' if n != 1 else ''}.")
            return True
        print(f"  ⚠  Webhook returned HTTP {r.status_code}: {r.text[:200]}")
        return False
    except Exception as e:
        print(f"  ⚠  Failed to send notification: {e}")
        return False
 
 
# ── Main ───────────────────────────────────────────────────────────────────────
 
def main():
    today       = date.today()
    lookback    = max(SENEDD_START, today - timedelta(days=LOOKBACK_DAYS))
    date_from   = lookback
    date_to     = today
 
    print(f"Senedd PRS Monitor — {today.isoformat()}")
    print(f"Fetching: {date_from} → {date_to}")
    print(f"Keywords: {len(KEYWORDS)}")
    print()
 
    # Load existing data
    os.makedirs("docs", exist_ok=True)
    existing = {}
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE) as f:
                stored = json.load(f)
            for q in stored.get("questions", []):
                existing[q["wq_ref"]] = q
            print(f"Loaded {len(existing)} existing questions from {DATA_FILE}")
        except Exception as e:
            print(f"Could not load existing data: {e}")
 
    # Fetch new questions
    seen = set(existing.keys())
    new_count = 0
 
    for i, kw in enumerate(KEYWORDS, 1):
        print(f"  [{i:02}/{len(KEYWORDS)}] '{kw}'...", end=" ", flush=True)
        hits = search_keyword(kw, date_from, date_to)
        added = 0
        for h in hits:
            if h["wq_ref"] not in seen:
                seen.add(h["wq_ref"])
                existing[h["wq_ref"]] = h
                added += 1
                new_count += 1
        print(f"{len(hits)} found, {added} new")
        time.sleep(0.3)
 
    # Count how many need fetching (new + unanswered)
    to_fetch = sum(1 for q in existing.values() if not (q.get("full_question") and q.get("answered_on")))
    print(f"\nFetching/refreshing {to_fetch} questions (new + unanswered)...")
    for i, (wq_ref, q) in enumerate(existing.items(), 1):
        if q.get("full_question") and q.get("answered_on"):
            continue  # already have full text and answer — no need to re-fetch
        detail = fetch_full_question(q["url_path"])
        q.update(detail)
        q["party"] = get_party(q.get("member_uid", ""))
        if i % 10 == 0:
            print(f"  {i}/{len(existing)}", flush=True)
        time.sleep(0.2)
 
    # ── Email notification for newly-identified questions ──
    # On the very first run after enabling notifications, mark all existing
    # questions as already-notified rather than sending a huge backlog email.
    first_email_run = not any("emailed" in q for q in existing.values())
 
    if first_email_run:
        for q in existing.values():
            q["emailed"] = True
        print("\n  ℹ  First run with notifications enabled — "
              "existing questions marked as seen, no email sent.")
    else:
        pending = [q for q in existing.values() if not q.get("emailed")]
        if pending:
            # Sort newest first for the email
            pending.sort(key=lambda q: parse_dmy(q.get("tabled_str", "")) or date.min,
                         reverse=True)
            if send_notification(pending):
                for q in pending:
                    q["emailed"] = True
            else:
                print("  ℹ  Questions left unmarked — will retry on next run.")
        else:
            print("\n  ℹ  No new questions to notify.")
 
    # Sort all questions by tabled date
    all_questions = sorted(
        existing.values(),
        key=lambda q: q.get("tabled_str", ""),
    )
 
    # Build weekly groups for the JSON
    weeks = defaultdict(list)
    for q in all_questions:
        d = parse_dmy(q.get("tabled_str", ""))
        if d and d >= SENEDD_START:
            weeks[week_commencing(d)].append(q)
 
    output = {
        "generated":      datetime.utcnow().isoformat() + "Z",
        "senedd_start":   SENEDD_START.isoformat(),
        "total":          len(all_questions),
        "questions":      all_questions,
        "weeks":          {
            w: qs for w, qs in sorted(weeks.items())
        },
    }
 
    with open(DATA_FILE, "w") as f:
        json.dump(output, f, indent=2)
 
    print(f"\n✅  Done. {len(all_questions)} total questions saved to {DATA_FILE}")
    print(f"    New this run: {new_count}")
 
 
if __name__ == "__main__":
    main()
 
 
 
