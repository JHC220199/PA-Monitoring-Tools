#!/usr/bin/env python3
"""
NRLA Parliament Hansard Monitor — Daily runner
Fetches yesterday's PRS-relevant parliamentary activity and uploads an HTML
briefing to SharePoint. A Power Automate flow watching that folder will then
email the briefing to the policy team.
 
Sources covered:
  - Written Ministerial Statements (made yesterday)
  - Hansard contributions (Commons, Lords, Grand Committee)
 
Required environment variables (all set as GitHub Secrets):
  AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET
  SHAREPOINT_SITE_NAME, SHAREPOINT_FOLDER_PATH, SHAREPOINT_HOST
"""
 
import os
import re
from datetime import datetime, timedelta
 
import requests
from msal import ConfidentialClientApplication
 
# ── Configuration ──────────────────────────────────────────────────────────────
 
TENANT_ID       = os.environ["AZURE_TENANT_ID"]
CLIENT_ID       = os.environ["AZURE_CLIENT_ID"]
CLIENT_SECRET   = os.environ["AZURE_CLIENT_SECRET"]
SITE_NAME       = os.environ.get("SHAREPOINT_SITE_NAME", "")  # Leave empty for root site
SHAREPOINT_LIB  = os.environ.get("SHAREPOINT_LIBRARY", "Documents")
FOLDER_PATH     = os.environ.get("SHAREPOINT_FOLDER_PATH", "Parliamentary Monitor/Daily Reports")
SHAREPOINT_HOST = os.environ.get("SHAREPOINT_HOST", "nrla.sharepoint.com")
 
TODAY = datetime.utcnow().date()
 
def get_last_sitting_day():
    """Returns the most recent weekday. On Monday this returns Friday."""
    day = TODAY - timedelta(days=1)
    while day.weekday() >= 5:  # 5=Saturday, 6=Sunday
        day -= timedelta(days=1)
    return day
 
YESTERDAY  = get_last_sitting_day()
DATE_STR   = YESTERDAY.strftime("%Y-%m-%d")
DATE_LABEL = YESTERDAY.strftime(f"%A {YESTERDAY.day} %B %Y")
 
# ── PRS keyword tiers (mirrors the WQ tool logic) ─────────────────────────────
 
EXCLUSIONS = [
    "social rented", "social housing", "council housing", "council tenant",
    "commercial landlord", "commercial tenant", "commercial rental",
    "commercial property", "commercial lease", "park home",
    "agricultural tenancy", "agricultural tenancies",
    "scotland", "scottish government", "scottish parliament",
    "holyrood", "wales", "welsh government", "senedd",
]
 
# Strong PRS signals — specific enough to include on their own
# Checked against apostrophe-normalised text so "Renters' Rights Act 2025" matches
STRONG_SIGNALS = [
    "private rented sector", "private rented", "private rental",
    "private landlord", "private tenant", "private renting",
    "privately rented", "rented sector", "rental sector",
    "renters rights act", "renters rights", "renters reform",
    "renters (reform)", "renting homes act",
    "section 21", "no-fault eviction", "no fault eviction",
    "pre-emptive eviction",
    "assured shorthold tenancy", "assured shorthold",
    "tenancy deposit", "deposit protection scheme",
    "letting agent", "landlord licensing", "landlord registration",
    "landlord database", "landlord accreditation", "rogue landlord",
    "buy-to-let", "hmo", "houses in multiple occupation",
    "house in multiple occupation", "property licensing",
    "selective licensing", "additional licensing", "mandatory licensing",
    "local housing allowance", "lha",
    "ground rent", "leasehold reform", "leasehold and commonhold",
    "commonhold", "right to enfranchise", "collective enfranchisement",
    "enfranchisement valuation", "estate management charge",
    "estate rent charge", "rent control", "rent stabilisation",
    "rent freeze", "rent cap", "rent determination", "rent tribunal",
    "build-to-rent", "rental accommodation", "rental housing",
    "rented properties", "rented homes", "rented housing",
    "tenant displacement", "right to rent",
    "section 8 notice", "section 13 notice",
    "decent homes standard",
]
 
# "landlord"/"tenant" alone are too broad in general debate context
# — require housing/tenancy context before including
LANDLORD_CONTEXT = [
    "tenant", "tenancy", "rented", "rental", "evict",
    "possession", "letting", "private", "lease",
]
 
# Energy terms — must appear with BOTH a rental word AND a housing/property
# word to avoid picking up unrelated energy debates
ENERGY_TERMS = [
    "energy performance certificate", "epc", "mees",
    "minimum energy efficiency", "eco4", "warm homes",
    "retrofit", "insulation",
]
ENERGY_RENTAL_ANCHORS = [
    "private rented", "rented home", "rented property",
    "rental property", "landlord", "tenant", "letting", "tenancy",
]
ENERGY_HOUSING_ANCHORS = [
    "home", "property", "propert", "dwelling",
    "house", "flat", "building",
]
 
# Leasehold — only with residential context
LEASEHOLD_TERMS = [
    "leasehold", "enfranchisement", "ground rent", "commonhold",
    "service charge", "managing agent", "right to manage",
    "leasehold reform",
]
LEASEHOLD_ANCHORS = [
    "residential", "flat", "apartment", "leaseholder",
]
 
 
# Signals that MUST match on a word boundary to avoid substring collisions
# e.g. "section 21" must not match "section 215"; "lha" must not match inside words;
# "hmo" must not match inside another token.
BOUNDARY_SENSITIVE = {
    "section 21", "section 8 notice", "section 13 notice",
    "lha", "hmo", "epc", "mees", "eco4",
}
 
# ── Body-text check terms ─────────────────────────────────────────────────────
# A DELIBERATELY TIGHT subset of unambiguous PRS terms, used only to decide
# whether a debate/statement with a NON-PRS title contains a genuine PRS
# intervention. These are terms that essentially only appear when someone is
# actually discussing the private rented sector — so a single boundary-safe
# match is a reliable signal of on-topic substance.
#
# Deliberately EXCLUDED from this body check (too collision-prone or too vague
# to trust on body text alone):
#   - bare "landlord" / "tenant"  (passing mentions — e.g. "absent landlords")
#   - "lha"                       (matches inside other tokens / abbreviations)
#   - "right to rent"             (collides with "right to work" statements)
#   - "rented homes" / "rented housing" / "rental housing" (generic phrasing)
SPECIFIC_PRS_TERMS = [
    "private rented sector", "private rented", "private rental",
    "private landlord", "private tenant", "private renting",
    "privately rented",
    "renters rights act", "renters rights", "renters reform",
    "renters (reform)", "renting homes act",
    "section 21", "no-fault eviction", "no fault eviction",
    "assured shorthold tenancy", "assured shorthold",
    "tenancy deposit", "deposit protection scheme",
    "letting agent", "landlord licensing", "landlord registration",
    "landlord database", "landlord accreditation", "rogue landlord",
    "buy-to-let", "hmo", "houses in multiple occupation",
    "house in multiple occupation", "selective licensing",
    "local housing allowance",
    "ground rent", "leasehold reform", "collective enfranchisement",
    "rent control", "rent stabilisation", "rent freeze", "rent cap",
    "rent tribunal", "build-to-rent",
    "section 8 notice", "section 13 notice",
    "decent homes standard",
]
 
 
def _signal_present(signal: str, text: str) -> bool:
    """Match a signal in text; use word boundaries for collision-prone signals."""
    if signal in BOUNDARY_SENSITIVE:
        # \b won't work well around digits/spaces, so assert a non-alphanumeric
        # (or string end) immediately after the signal
        pattern = re.escape(signal) + r"(?![a-z0-9])"
        return re.search(pattern, text) is not None
    return signal in text
 
 
def is_prs_relevant(text: str) -> bool:
    """
    Determine PRS relevance using a tiered keyword approach.
    Normalises apostrophes so "Renters' Rights Act 2025" matches correctly.
    """
    if not text:
        return False
 
    lower = text.lower()
 
    # Normalise smart/curly apostrophes then strip all apostrophes for matching
    normalised = lower.replace("\u2019", "'").replace("\u2018", "'").replace("'", "")
 
    # Hard exclusions
    if any(excl in lower for excl in EXCLUSIONS):
        return False
 
    # Strong PRS signals (checked on apostrophe-normalised text)
    if any(_signal_present(sig, normalised) for sig in STRONG_SIGNALS):
        return True
 
    # "landlord" — only include when paired with tenancy/housing context
    if "landlord" in lower and any(ctx in lower for ctx in LANDLORD_CONTEXT):
        return True
 
    # "tenant" — only include when not commercial and paired with rental context
    if "tenant" in lower and "commercial" not in lower:
        if any(ctx in lower for ctx in LANDLORD_CONTEXT):
            return True
 
    # Energy efficiency — only with BOTH rental AND housing/property context
    if any(e in lower for e in ENERGY_TERMS):
        has_rental  = any(r in lower for r in ENERGY_RENTAL_ANCHORS)
        has_housing = any(h in lower for h in ENERGY_HOUSING_ANCHORS)
        if has_rental and has_housing:
            return True
 
    # Leasehold — only with residential context
    if any(lh in lower for lh in LEASEHOLD_TERMS):
        if any(a in lower for a in LEASEHOLD_ANCHORS):
            return True
 
    return False
 
 
# ── Parliament API helpers ─────────────────────────────────────────────────────
 
 
 
 
def fetch_written_statements() -> list:
    """Fetch written ministerial statements made on the target date."""
    results = []
    url = (
        f"https://questions-statements-api.parliament.uk/api/writtenstatements/statements"
        f"?madeWhenFrom={DATE_STR}&madeWhenTo={DATE_STR}&take=100"
    )
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        for s in data.get("results", []):
            value = s.get("value", {})
            text  = value.get("text", "")
            title = value.get("title", "").strip()
 
            # Same two-tier test as debates: include if the TITLE is PRS-relevant,
            # or the statement body contains an unambiguous specific PRS term.
            # Using the specific-body check (not the broad is_prs_relevant) keeps
            # out statements that only mention a PRS term in passing — e.g. a
            # "right to work" statement that references the "right to rent" scheme.
            title_relevant = is_prs_relevant(title)
            body_relevant  = _has_specific_prs_signal(text)
 
            if title_relevant or body_relevant:
                uin       = value.get("uin", "")
                made_date = (value.get("dateMade", "") or "")[:10]  # YYYY-MM-DD
                if uin and made_date:
                    link = f"https://questions-statements.parliament.uk/written-statements/detail/{made_date}/{uin}"
                else:
                    link = "https://questions-statements.parliament.uk/written-statements"
                type_label = "Written Statement" if title_relevant else "Written Statement (PRS point raised)"
                results.append({
                    "chamber": value.get("house", "Unknown"),
                    "type":    type_label,
                    "title":   title,
                    "speaker": value.get("memberRole", "").strip() or value.get("answeringBodyName", ""),
                    "excerpt": text[:300],
                    "link":    link,
                })
    except Exception as e:
        print(f"Written statements error: {e}")
    return results
 
 
def _has_specific_prs_signal(text: str) -> bool:
    """
    Body-text relevance test. Requires an UNAMBIGUOUS PRS term from
    SPECIFIC_PRS_TERMS (not just a passing mention of "landlord"/"tenant", and
    not collision-prone terms like bare "lha" or "right to rent"). Used to catch
    genuine PRS interventions inside debates/statements whose title is not
    itself PRS-related. Boundary-safe so "section 21" never matches "section 215".
    """
    if not text:
        return False
    lower = text.lower()
    normalised = lower.replace("\u2019", "'").replace("\u2018", "'").replace("'", "")
    if any(excl in lower for excl in EXCLUSIONS):
        return False
    return any(_signal_present(term, normalised) for term in SPECIFIC_PRS_TERMS)
 
 
def _slugify(title: str) -> str:
    """Convert a debate title into the URL slug Hansard uses (CamelCase, no spaces/punctuation)."""
    # Remove punctuation, split into words, capitalise each, join
    cleaned = re.sub(r"[^a-zA-Z0-9 ]", "", title or "")
    words   = cleaned.split()
    return "".join(w.capitalize() for w in words) or "Debate"
 
 
def fetch_hansard() -> list:
    """
    Search Hansard for PRS-relevant contributions using the hansard-api endpoint
    (the public front-end at hansard.parliament.uk is behind Cloudflare and cannot
    be queried directly). Groups results by debate so each debate appears once.
    """
    results        = []
    seen_debates   = set()   # dedupe by DebateSectionExtId
    search_terms   = [
        "private rented sector", "landlord", "section 21",
        "local housing allowance", "letting agent", "tenancy deposit",
        "renters rights", "renters reform", "leasehold", "HMO",
        "assured tenancy", "ground rent", "buy-to-let",
        "selective licensing", "rent control", "no-fault eviction",
    ]
 
    for term in search_terms:
        url = (
            f"https://hansard-api.parliament.uk/search/contributions/Spoken.json"
            f"?queryParameters.searchTerm={requests.utils.quote(term)}"
            f"&queryParameters.startDate={DATE_STR}"
            f"&queryParameters.endDate={DATE_STR}"
            f"&queryParameters.take=50"
        )
        try:
            resp = requests.get(url, timeout=20, headers={"User-Agent": "NRLA-Monitor/1.0"})
            resp.raise_for_status()
            data = resp.json()
 
            for item in data.get("Results", []):
                debate_ext_id = item.get("DebateSectionExtId")
                if not debate_ext_id or debate_ext_id in seen_debates:
                    continue
 
                # Build the full text to test for relevance
                full_text = item.get("ContributionTextFull") or item.get("ContributionText") or ""
                # Strip HTML tags from the excerpt
                clean_text = re.sub(r"<[^>]+>", "", full_text).strip()
                debate_title = item.get("DebateSection", "")
 
                # Two ways a debate qualifies:
                #  (a) the debate TITLE is PRS-relevant — the whole debate is
                #      on-topic (reliable, catches dedicated PRS debates), OR
                #  (b) THIS contribution's body contains an unambiguous, specific
                #      PRS term (from SPECIFIC_PRS_TERMS, boundary-safe) — catches
                #      genuine PRS interventions inside a broadly-titled debate.
                #
                # Bare "landlord"/"tenant" mentions and collision-prone terms do
                # NOT qualify on body text alone, which keeps out the planning /
                # high-street / right-to-work style false positives.
                title_relevant = is_prs_relevant(debate_title)
                body_relevant  = _has_specific_prs_signal(clean_text)
 
                if not (title_relevant or body_relevant):
                    continue
 
                seen_debates.add(debate_ext_id)
 
                # Determine chamber — the Section field flags Grand Committee etc.
                house   = item.get("House", "")
                section = item.get("Section", "")
                if "grand committee" in section.lower():
                    chamber = "Grand Committee"
                elif house == "Commons":
                    chamber = "House of Commons"
                elif house == "Lords":
                    chamber = "House of Lords"
                else:
                    chamber = house or "Unknown"
 
                # Build the public Hansard debate URL
                sitting = (item.get("SittingDate", "") or "")[:10]  # YYYY-MM-DD
                slug    = _slugify(debate_title)
                house_path = house if house in ("Commons", "Lords") else "Commons"
                link = f"https://hansard.parliament.uk/{house_path}/{sitting}/debates/{debate_ext_id}/{slug}"
 
                # Flag debates matched on a specific intervention (not the title)
                # so the reader knows why a broadly-titled debate is included.
                matched_note = "" if title_relevant else " (PRS point raised in debate)"
 
                results.append({
                    "chamber": chamber,
                    "type":    "Debate" + matched_note,
                    "title":   debate_title,
                    "speaker": item.get("AttributedTo", "") or item.get("MemberName", ""),
                    "excerpt": clean_text[:300],
                    "link":    link,
                })
        except Exception as e:
            print(f"Hansard search error ({term}): {e}")
 
    return results
 
# ── Email HTML builder ─────────────────────────────────────────────────────────
 
CHAMBER_COLOURS = {
    "Commons":         "#006400",
    "Lords":           "#722F37",
    "Grand Committee": "#B8860B",
    "Committees":      "#1a5276",
}
 
 
def build_html_email(items: list) -> tuple:
    """Return (subject, html_body) for the daily digest."""
    count = len(items)
    subject = (
        f"Parliamentary Monitor: {count} PRS item{'s' if count != 1 else ''} — {DATE_LABEL}"
        if count > 0
        else f"Parliamentary Monitor: No PRS activity — {DATE_LABEL}"
    )
 
    commons_n = sum(1 for i in items if "commons" in i["chamber"].lower())
    lords_n   = sum(1 for i in items if "lords" in i["chamber"].lower() and "grand" not in i["chamber"].lower())
    gc_n      = sum(1 for i in items if "grand" in i["chamber"].lower())
    sc_n      = sum(1 for i in items if "committee" in i["chamber"].lower() and "grand" not in i["chamber"].lower())
 
    grouped = {}
    for item in items:
        grouped.setdefault(item["chamber"], []).append(item)
 
    sections_html = ""
    for chamber, chamber_items in grouped.items():
        colour = CHAMBER_COLOURS.get(chamber, "#333333")
        rows   = ""
        for it in chamber_items:
            rows += f"""
            <tr>
              <td style="padding:12px 0;border-bottom:1px solid #eee;">
                <strong style="font-size:14px;">{it['title'] or it['type']}</strong><br>
                <span style="color:#666;font-size:12px;">{it['speaker']} &bull; {it['type']}</span><br>
                <p style="margin:6px 0;font-size:13px;color:#333;">{it['excerpt']}…</p>
                <a href="{it['link']}" style="font-size:12px;color:#113B54;">View in Hansard →</a>
              </td>
            </tr>"""
        sections_html += f"""
        <tr>
          <td style="background:{colour};color:#fff;padding:10px 16px;font-size:15px;font-weight:bold;letter-spacing:0.5px;">
            {chamber}
          </td>
        </tr>
        <tr><td><table width="100%" cellpadding="0" cellspacing="0">{rows}</table></td></tr>
        """
 
    no_activity = "" if count > 0 else """
        <tr><td style="padding:24px;text-align:center;color:#666;font-size:14px;">
            No PRS-relevant activity recorded for this date.
        </td></tr>"""
 
    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;font-family:Arial,sans-serif;background:#f4f4f4;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f4;padding:20px 0;">
<tr><td align="center">
<table width="620" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:6px;overflow:hidden;">
 
  <tr><td style="background:#113B54;padding:20px 24px;">
    <p style="margin:0;color:#fff;font-size:11px;text-transform:uppercase;letter-spacing:1px;">NRLA</p>
    <h1 style="margin:4px 0 0;color:#fff;font-size:20px;">Parliamentary Monitor</h1>
    <p style="margin:4px 0 0;color:#aac4d4;font-size:13px;">{DATE_LABEL}</p>
  </td></tr>
 
  <tr><td style="background:#E96C19;padding:10px 24px;">
    <table width="100%" cellpadding="0" cellspacing="0"><tr>
      <td style="color:#fff;font-size:13px;text-align:center;"><strong>{commons_n}</strong><br>Commons</td>
      <td style="color:#fff;font-size:13px;text-align:center;"><strong>{lords_n}</strong><br>Lords</td>
      <td style="color:#fff;font-size:13px;text-align:center;"><strong>{gc_n}</strong><br>Grand Cmte</td>
      <td style="color:#fff;font-size:13px;text-align:center;"><strong>{sc_n}</strong><br>Committees</td>
      <td style="color:#fff;font-size:13px;text-align:center;"><strong>{count}</strong><br>Total</td>
    </tr></table>
  </td></tr>
 
  <tr><td style="padding:0 24px;">
    <table width="100%" cellpadding="0" cellspacing="0">
      {sections_html}{no_activity}
    </table>
  </td></tr>
 
  <tr><td style="background:#f9f9f9;padding:14px 24px;border-top:1px solid #eee;">
    <p style="margin:0;font-size:11px;color:#999;">
      Sources: Hansard, Written Questions &amp; Statements API — parliament.uk<br>
      Generated automatically on {TODAY.strftime(f"{TODAY.day} %B %Y")} · NRLA Parliamentary Monitor
    </p>
  </td></tr>
 
</table>
</td></tr></table>
</body></html>"""
 
    return subject, html
 
# ── SharePoint upload via Microsoft Graph ─────────────────────────────────────
 
def get_access_token() -> str:
    app = ConfidentialClientApplication(
        client_id=CLIENT_ID,
        client_credential=CLIENT_SECRET,
        authority=f"https://login.microsoftonline.com/{TENANT_ID}",
    )
    result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in result:
        raise RuntimeError(f"Token acquisition failed: {result.get('error_description')}")
    return result["access_token"]
 
 
def get_sharepoint_site_id(token: str) -> str:
    # If SITE_NAME is set, target a subsite; otherwise use the root site
    if SITE_NAME:
        url = f"https://graph.microsoft.com/v1.0/sites/{SHAREPOINT_HOST}:/sites/{SITE_NAME}"
    else:
        url = f"https://graph.microsoft.com/v1.0/sites/{SHAREPOINT_HOST}"
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=20)
    resp.raise_for_status()
    return resp.json()["id"]
 
 
def get_drive_id(token: str, site_id: str) -> str:
    """Get the drive ID for the SHAREPOINT_LIBRARY document library."""
    url   = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drives"
    resp  = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=20)
    resp.raise_for_status()
    drives = resp.json().get("value", [])
    for drive in drives:
        if drive.get("name", "").lower() == SHAREPOINT_LIB.lower():
            return drive["id"]
    if drives:
        print(f"Warning: library '{SHAREPOINT_LIB}' not found — using default drive")
        return drives[0]["id"]
    raise RuntimeError("No drives found on SharePoint site")
 
 
def upload_to_sharepoint(token: str, site_id: str, drive_id: str, filename: str, html_content: str):
    """Upload an HTML file to the correct SharePoint library via Graph API."""
    encoded_path = requests.utils.quote(f"{FOLDER_PATH}/{filename}")
    url  = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{encoded_path}:/content"
    resp = requests.put(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type":  "text/html",
        },
        data=html_content.encode("utf-8"),
        timeout=30,
    )
    resp.raise_for_status()
    print(f"Uploaded to SharePoint library '{SHAREPOINT_LIB}': {filename}")
 
# ── Main ───────────────────────────────────────────────────────────────────────
 
def main():
    print(f"Running Parliament PRS Monitor for {DATE_LABEL}…")
 
    items  = []
    items += fetch_written_statements()
    items += fetch_hansard()
 
    print(f"Found {len(items)} PRS-relevant items")
 
    if not items:
        print("No PRS-relevant activity found — skipping SharePoint upload. No email will be sent.")
        return
 
    subject, html = build_html_email(items)
    filename      = f"parliament-monitor-{DATE_STR}.html"
 
    token    = get_access_token()
    site_id  = get_sharepoint_site_id(token)
    drive_id = get_drive_id(token, site_id)
    upload_to_sharepoint(token, site_id, drive_id, filename, html)
 
    print(f"Done. Subject: {subject}")
 
 
if __name__ == "__main__":
    main()
 
