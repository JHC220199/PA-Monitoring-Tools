import os
import json
import requests
from datetime import datetime, timedelta
from msal import ConfidentialClientApplication
 
# ── Configuration ─────────────────────────────────────────────────────────────
 
TENANT_ID         = os.environ["AZURE_TENANT_ID"]
CLIENT_ID         = os.environ["AZURE_CLIENT_ID"]
CLIENT_SECRET     = os.environ["AZURE_CLIENT_SECRET"]
SITE_NAME         = os.environ.get("SHAREPOINT_SITE_NAME", "")
LIBRARY_NAME      = os.environ.get("SHAREPOINT_LIBRARY", "Documents")
FOLDER_PATH       = os.environ.get("SHAREPOINT_FOLDER_PATH", "Parliamentary Monitor/Daily Reports")
SHAREPOINT_HOST   = os.environ.get("SHAREPOINT_HOST", "rlateam.sharepoint.com")
 
TODAY      = datetime.utcnow().date()
YESTERDAY  = TODAY - timedelta(days=1)
DATE_STR   = YESTERDAY.strftime("%Y-%m-%d")
DATE_LABEL = YESTERDAY.strftime("%A %-d %B %Y")
 
# ── PRS keyword tiers (mirrors the WQ tool logic) ─────────────────────────────
 
STRONG_SIGNALS = [
    "section 21", "section 8", "no-fault eviction", "assured shorthold",
    "tenancy deposit", "letting agent", "landlord", "landlords", "private rented",
    "private rented sector", "private rental", "rented properties", "rented homes",
    "rented housing", "hmo", "houses in multiple occupation", "build-to-rent",
    "rent arrears", "rent control", "rent freeze", "rent cap", "rent stabilisation",
    "local housing allowance", "lha", "housing benefit", "renters reform",
    "renters rights", "renters (reform)", "decent homes standard",
    "decent homes", "property licensing", "selective licensing",
    "additional licensing", "mandatory licensing",
]
 
CONTEXTUAL_SIGNALS = [
    "energy efficiency", "epc", "mees", "minimum energy efficiency",
    "eco4", "warm homes", "retrofit", "insulation",
]
 
CONTEXT_ANCHORS = [
    "landlord", "tenant", "rental", "rented", "private sector",
    "letting", "tenancy",
]
 
LEASEHOLD_TERMS = [
    "leasehold", "enfranchisement", "ground rent", "commonhold",
    "service charge", "managing agent", "right to manage",
    "leasehold reform",
]
 
LEASEHOLD_ANCHORS = [
    "residential", "flat", "apartment", "leaseholder",
]
 
EXCLUSIONS = [
    "social rented", "social housing", "council housing", "council tenant",
    "commercial landlord", "commercial tenant", "commercial rental",
    "commercial property", "park home", "agricultural tenancy",
    "agricultural tenancies",
]
 
 
def is_prs_relevant(text: str) -> bool:
    """Apply the three-tier keyword logic to determine PRS relevance."""
    lower = text.lower()
    if any(excl in lower for excl in EXCLUSIONS):
        return False
    if any(sig in lower for sig in STRONG_SIGNALS):
        return True
    if any(ctx in lower for ctx in CONTEXTUAL_SIGNALS):
        if any(anc in lower for anc in CONTEXT_ANCHORS):
            return True
    if any(lh in lower for lh in LEASEHOLD_TERMS):
        if any(anc in lower for anc in LEASEHOLD_ANCHORS):
            return True
    return False
 
 
# ── Parliament API helpers ─────────────────────────────────────────────────────
 
def fetch_written_questions() -> list[dict]:
    """Fetch written questions answered or tabled on the target date."""
    results = []
    url = (
        f"https://questions-statements-api.parliament.uk/api/writtenquestions/questions"
        f"?answeredWhenFrom={DATE_STR}&answeredWhenTo={DATE_STR}&take=100"
    )
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        for q in data.get("results", []):
            value = q.get("value", {})
            question_text = value.get("questionText", "")
            answer_text   = value.get("answerText", "")
            combined      = f"{question_text} {answer_text}"
            if is_prs_relevant(combined):
                results.append({
                    "chamber":  value.get("house", "Unknown"),
                    "type":     "Written Question",
                    "title":    value.get("answeringBodyName", ""),
                    "speaker":  value.get("askingMemberName", ""),
                    "excerpt":  question_text[:300],
                    "link":     f"https://questions-statements-api.parliament.uk/api/writtenquestions/questions/{value.get('id', '')}",
                })
    except Exception as e:
        print(f"Written questions error: {e}")
    return results
 
 
def fetch_written_statements() -> list[dict]:
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
            if is_prs_relevant(text):
                results.append({
                    "chamber":  value.get("house", "Unknown"),
                    "type":     "Written Statement",
                    "title":    value.get("title", ""),
                    "speaker":  value.get("memberName", ""),
                    "excerpt":  text[:300],
                    "link":     f"https://questions-statements-api.parliament.uk/api/writtenstatements/statements/{value.get('id', '')}",
                })
    except Exception as e:
        print(f"Written statements error: {e}")
    return results
 
 
def fetch_hansard() -> list[dict]:
    """Search Hansard for PRS-relevant debates."""
    results     = []
    search_terms = [
        "private rented sector", "landlord tenant", "section 21",
        "local housing allowance", "letting agent", "tenancy deposit",
        "renters reform", "leasehold residential", "HMO licensing",
    ]
    seen_ids = set()
    for term in search_terms:
        url = (
            f"https://hansard.parliament.uk/search/Contributions"
            f"?queryParameters.searchTerm={requests.utils.quote(term)}"
            f"&queryParameters.startDate={DATE_STR}"
            f"&queryParameters.endDate={DATE_STR}"
            f"&queryParameters.take=20"
        )
        try:
            resp = requests.get(url, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            for item in data.get("Contributions", []):
                contrib_id = item.get("ContributionId")
                if contrib_id in seen_ids:
                    continue
                seen_ids.add(contrib_id)
                text = f"{item.get('DebateSection', '')} {item.get('Value', '')}"
                if is_prs_relevant(text):
                    chamber_raw = item.get("House", "")
                    chamber     = "Grand Committee" if "grand" in chamber_raw.lower() else chamber_raw
                    results.append({
                        "chamber":  chamber,
                        "type":     item.get("ContributionType", "Debate"),
                        "title":    item.get("DebateSection", ""),
                        "speaker":  item.get("AttributedTo", ""),
                        "excerpt":  item.get("Value", "")[:300],
                        "link":     f"https://hansard.parliament.uk{item.get('HansardMemberUrl', '')}",
                    })
        except Exception as e:
            print(f"Hansard search error ({term}): {e}")
    return results
 
 
# ── Email HTML builder ─────────────────────────────────────────────────────────
 
CHAMBER_COLOURS = {
    "Commons":        "#006400",
    "Lords":          "#722F37",
    "Grand Committee":"#B8860B",
    "Committees":     "#1a5276",
}
 
 
def build_html_email(items: list[dict]) -> tuple[str, str]:
    """Return (subject, html_body) for the daily digest."""
    count = len(items)
    subject = (
        f"Parliamentary Monitor: {count} PRS item{'s' if count != 1 else ''} — {DATE_LABEL}"
        if count > 0
        else f"Parliamentary Monitor: No PRS activity — {DATE_LABEL}"
    )
 
    commons_n = sum(1 for i in items if "commons" in i["chamber"].lower())
    lords_n   = sum(1 for i in items if "lords"   in i["chamber"].lower() and "grand" not in i["chamber"].lower())
    gc_n      = sum(1 for i in items if "grand"   in i["chamber"].lower())
    sc_n      = sum(1 for i in items if "committee" in i["chamber"].lower() and "grand" not in i["chamber"].lower())
 
    grouped: dict[str, list] = {}
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
<html>
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;font-family:Arial,sans-serif;background:#f4f4f4;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f4;padding:20px 0;">
  <tr><td align="center">
    <table width="620" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:6px;overflow:hidden;">
 
      <!-- Header -->
      <tr><td style="background:#113B54;padding:20px 24px;">
        <p style="margin:0;color:#fff;font-size:11px;text-transform:uppercase;letter-spacing:1px;">NRLA</p>
        <h1 style="margin:4px 0 0;color:#fff;font-size:20px;">Parliamentary Monitor</h1>
        <p style="margin:4px 0 0;color:#aac4d4;font-size:13px;">{DATE_LABEL}</p>
      </td></tr>
 
      <!-- Summary strip -->
      <tr><td style="background:#E96C19;padding:10px 24px;">
        <table width="100%" cellpadding="0" cellspacing="0"><tr>
          <td style="color:#fff;font-size:13px;text-align:center;"><strong>{commons_n}</strong><br>Commons</td>
          <td style="color:#fff;font-size:13px;text-align:center;"><strong>{lords_n}</strong><br>Lords</td>
          <td style="color:#fff;font-size:13px;text-align:center;"><strong>{gc_n}</strong><br>Grand Cmte</td>
          <td style="color:#fff;font-size:13px;text-align:center;"><strong>{sc_n}</strong><br>Committees</td>
          <td style="color:#fff;font-size:13px;text-align:center;"><strong>{count}</strong><br>Total</td>
        </tr></table>
      </td></tr>
 
      <!-- Results -->
      <tr><td style="padding:0 24px;">
        <table width="100%" cellpadding="0" cellspacing="0">
          {sections_html}{no_activity}
        </table>
      </td></tr>
 
      <!-- Footer -->
      <tr><td style="background:#f9f9f9;padding:14px 24px;border-top:1px solid #eee;">
        <p style="margin:0;font-size:11px;color:#999;">
          Sources: Hansard, Written Questions &amp; Statements API — parliament.uk<br>
          Generated automatically on {TODAY.strftime("%-d %B %Y")} · NRLA Parliamentary Monitor
        </p>
      </td></tr>
 
    </table>
  </td></tr>
</table>
</body>
</html>"""
 
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
    # Root site (no /sites/SiteName in URL) — query by hostname only
    if not SITE_NAME:
        url = f"https://graph.microsoft.com/v1.0/sites/{SHAREPOINT_HOST}"
    else:
        url = f"https://graph.microsoft.com/v1.0/sites/{SHAREPOINT_HOST}:/sites/{SITE_NAME}"
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=20)
    resp.raise_for_status()
    return resp.json()["id"]
 
 
def get_drive_id(token: str, site_id: str) -> str:
    """Find the drive ID for the named document library."""
    url  = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drives"
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=20)
    resp.raise_for_status()
    drives = resp.json().get("value", [])
    for drive in drives:
        if drive.get("name", "").lower() == LIBRARY_NAME.lower():
            print(f"Found drive: {drive['name']} ({drive['id']})")
            return drive["id"]
    available = [d.get("name") for d in drives]
    raise RuntimeError(f"Drive '{LIBRARY_NAME}' not found. Available drives: {available}")
 
 
def upload_to_sharepoint(token: str, site_id: str, filename: str, html_content: str):
    """Upload an HTML file to the correct SharePoint document library via Graph API."""
    drive_id     = get_drive_id(token, site_id)
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
    print(f"Uploaded to SharePoint: {filename}")
 
 
# ── Main ───────────────────────────────────────────────────────────────────────
 
def main():
    print(f"Running Parliament PRS Monitor for {DATE_LABEL}…")
 
    items  = []
    items += fetch_written_questions()
    items += fetch_written_statements()
    items += fetch_hansard()
 
    print(f"Found {len(items)} PRS-relevant items")
 
    subject, html = build_html_email(items)
    filename      = f"parliament-monitor-{DATE_STR}.html"
 
    token   = get_access_token()
    site_id = get_sharepoint_site_id(token)
    upload_to_sharepoint(token, site_id, filename, html)
 
    print(f"Done. Subject: {subject}")
 
 
if __name__ == "__main__":
    main()
 
