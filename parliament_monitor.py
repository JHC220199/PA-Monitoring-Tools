#!/usr/bin/env python3
"""
Parliament PRS Monitor — Daily updater
Fetches written questions about the private residential rented sector
and saves them to docs/data.json for the web dashboard.

No external dependencies — uses only Python stdlib + curl.
"""

import json, os, re, subprocess, time
from datetime import datetime, timedelta
from urllib.parse import urlencode

# ── Configuration ──────────────────────────────────────────────────────────────

DATA_FILE = os.path.join('docs', 'data.json')

QUESTIONS_API = 'https://questions-statements-api.parliament.uk/api/writtenquestions/questions'
MEMBERS_API   = 'https://members-api.parliament.uk/api/Members'

# Search terms sent to the Parliament API
KEYWORDS = [
    # Core PRS tenancy
    'landlord',
    'section 21',
    'renters rights',
    'rent repayment order',
    'tenancy deposit',
    'assured shorthold',
    'private rented',
    'private renting',
    'letting agent',
    'landlord licensing',
    # Rent levels / market
    'rent control',
    'rent stabilisation',
    'rent inflation',
    'rent determination',
    'build-to-rent',
    'rental accommodation',
    'rental housing',
    # Leasehold
    'leasehold',
    'leaseholder',
    'ground rent',
    'commonhold',
    # Benefits / LHA
    'lha freeze',
    'lha rates',
    'housing allowance freeze',
    'housing benefit landlord',
    # Regulation / enforcement
    'prs database',
    'prs ombudsman',
    'rogue landlord',
    'decent homes',
    # Energy efficiency (broad — affects landlords via MEES)
    'energy company obligation',
    'eco4',
    'warm home',
    'minimum energy efficiency',
    'energy performance certificate',
    'rented homes energy',
    'private rented energy',
    'retrofit housing',
    # Other
    'making tax digital landlord',
    'landlord registration',
    'property licensing landlord',
    'personal independence payment rent',
]

PARTY_MAP = {
    'Labour': 'Lab', 'Conservative': 'Con', 'Liberal Democrat': 'LD',
    'Scottish National Party': 'SNP', 'Crossbench': 'CB', 'Independent': 'Ind',
    'Plaid Cymru': 'PC', 'Reform UK': 'Ref', 'Green Party': 'Green',
    'Democratic Unionist Party': 'DUP', 'Non-affiliated': 'Non-aff',
    'Alliance Party': 'Alliance', 'Ulster Unionist Party': 'UUP',
    'Social Democratic and Labour Party': 'SDLP',
}

# ── HTTP helper ────────────────────────────────────────────────────────────────

def http_get(url, timeout=25):
    r = subprocess.run(
        ['curl', '-s', f'--max-time', str(timeout), '-H', 'User-Agent: PRS-Monitor/1.0', url],
        capture_output=True, text=True
    )
    try:
        return json.loads(r.stdout) if r.stdout.strip() else None
    except json.JSONDecodeError:
        return None

# ── Date / format helpers ──────────────────────────────────────────────────────

def ordinal(n):
    s = ['th', 'st', 'nd', 'rd']
    v = n % 100
    return f"{n}{s[(v-20)%10] if (v-20)%10 < 4 else s[v] if v < 4 else s[0]}"

def fmt_date(iso):
    if not iso:
        return ''
    d = datetime.fromisoformat(iso[:19])
    return f"{ordinal(d.day)} {d.strftime('%B')}"

def monday_of(iso):
    d = datetime.fromisoformat(iso[:19]).date()
    return d - timedelta(days=d.weekday())

def q_url(date_tabled, uin):
    return f"https://questions-statements.parliament.uk/written-questions/detail/{date_tabled[:10]}/{uin}"

# ── PRS relevance filter ───────────────────────────────────────────────────────

def is_prs(text):
    """
    Returns True if the question relates to the private residential rented sector,
    leasehold reform, housing energy efficiency, or related housing policy.
    """
    t = text.lower()

    # ── Check whether residential/landlord context is present ─────────────────
    has_residential = bool(re.search(
        r'\b(landlord|tenant|tenancy|leaseholder|private rent|rented|rental|renters?|letting)\b', t
    ))

    # ── Hard exclusions — only apply when there is no residential context ──────
    # (e.g. a question about non-domestic EPC that ALSO mentions landlords is kept)
    if not has_residential:
        if re.search(r'\b(regulator of social housing|housing association|social landlord|'
                     r'social rented sector|social housing stock|housing trust|registered provider)\b', t):
            return False
        if re.search(r'\b(commercial tenant|commercial landlord|commercial rental|'
                     r'commercial lease|commercial property|commercial premises|'
                     r'industrial unit|retail premises|business premises|'
                     r'farm business tenancy|agricultural tenancy)\b', t):
            return False

    # Social housing: always exclude unless residential/landlord context present
    if re.search(r'\b(regulator of social housing|housing association)\b', t) and not has_residential:
        return False

    # ── Strong direct signals — any one is sufficient to include ───────────────
    strong = [
        # Core PRS
        r'private rented sector', r'private renter', r'private rental',
        r'private landlord', r'private tenant', r'private renting',
        r'rented sector', r'rental sector',
        r'renters.{0,5}rights act', r'renters\b',
        r'rent repayment', r'section 21', r'assured shorthold', r'tenancy deposit',
        r'buy.to.let', r'build.to.rent',
        r'\bhmo\b', r'house in multiple occupation',
        # Benefits
        r'local housing allowance', r'\blha\b',
        r'lha (rate|level|freeze|cap)', r'housing allowance (freeze|rate|level)',
        # Leasehold
        r'ground rent',
        r'leasehold reform', r'leasehold and commonhold', r'commonhold',
        r'leasehold enfranchis', r'leaseholder', r'leasehold house', r'leasehold flat',
        r'right to manage', r'managing agent', r'right to rent', r'letting agent',
        # Landlord regulation
        r'landlord licens', r'landlord registr', r'landlord database',
        r'institutional landlord',
        # Eviction / possession
        r'no.fault eviction', r'section 21 eviction', r'pre.emptive eviction',
        # Rent / tribunal
        r'rent appeal', r'property chamber',
        r'section 13 rent', r'rent determination', r'market rent determination',
        r'rent control', r'rent stabilisation', r'rent inflation',
        r'rent freeze', r'rent cap',
        # Standards / regulation
        r'decent homes standard', r'decent homes',
        r'prs database', r'prs ombudsman', r'landlord ombudsman',
        # Energy (with explicit PRS/housing context)
        r'warm homes plan', r'warm home',
        r'energy company obligation', r'\beco4\b',
        r'awaab',
        # Enforcement
        r'rogue landlord',
        r'civil penalt.{0,20}landlord', r'landlord.{0,20}civil penalt',
        # Other PRS
        r'spray foam insulation',
        r'guarantor.{0,20}(rent|tenancy)',
        r'making tax digital.{0,20}landlord', r'landlord.{0,20}making tax digital',
        r'rental income.{0,20}(tax|hmrc)',
        r'licensed accommodation',
        # Rental market broadly
        r'rented propert',       # "rented properties", "rented property"
        r'rented home',          # "rented homes"
        r'rental housing',
        r'rental accommodation',
        r'rental market',
        r'private rent inflation',
        r'multifamily.{0,30}(rent|housing|sector)',
    ]
    for p in strong:
        if re.search(p, t):
            return True

    # ── Contextual: landlord + residential housing ─────────────────────────────
    if re.search(r'\blandlord\b', t) and re.search(
        r'\b(tenant|tenancy|rented|possession|evict|rent|letting|dwelling|home|flat|house)\b', t
    ):
        return True

    # ── Energy efficiency for HOMES / HOUSING (residential, not commercial) ────
    # Include broadly — EPC/MEES/retrofit policy all affects landlords and tenants
    energy_terms = r'\b(energy efficiency|energy performance|epc|mees|minimum energy efficiency|' \
                   r'retrofit|insulation|warm homes|eco4|energy company obligation)\b'
    home_terms   = r'\b(home|house|housing|homes|houses|domestic|dwelling|rural|' \
                   r'residential|building stock|housing stock|solid wall|buildings?)\b'

    if re.search(energy_terms, t) and re.search(home_terms, t):
        # Exclude if ONLY about purely commercial/industrial with no residential signal
        if not re.search(r'\b(purely commercial|industrial build|office build|retail store|commercial build)\b', t):
            return True

    # ── EPC methodology / standards broadly (affects rental compliance) ────────
    if re.search(r'\bepc\b', t) and re.search(r'\b(standard|rating|valuation|methodology|compliance|certificate|band)\b', t):
        return True

    # ── UC / housing benefit paid to private landlords ─────────────────────────
    if re.search(r'(universal credit|housing benefit|housing element).{0,80}(landlord|private rent)', t):
        return True
    if re.search(r'housing (benefit|element).{0,60}(private|rented|rental|tenant)', t):
        return True

    # ── Residential leasehold ──────────────────────────────────────────────────
    if re.search(r'\bleasehold\b', t):
        if re.search(
            r'\b(leaseholder|ground rent|service charge|residential|flat|commonhold|'
            r'enfranchis|reform bill|freehold reform|managing agent|right to manage|'
            r'estate management|estate rent charge)\b', t
        ):
            return True

    # ── LHA / housing allowance ────────────────────────────────────────────────
    if re.search(r'housing allowance', t) and re.search(
        r'\b(rent|rented|private|tenant|poverty|homelessness)\b', t
    ):
        return True

    # ── "Standards of rented X" / "renting properties" etc ───────────────────
    if re.search(r'\b(standard|condition|quality).{0,30}rented\b', t):
        return True
    if re.search(r'\brented\b.{0,30}\b(standard|condition|quality|propert|home|sector)\b', t):
        return True

    # ── PIP / disability benefits used to cover rent ───────────────────────────
    if re.search(r'personal independence payment', t) and re.search(r'\b(rent|housing cost|housing benefit)\b', t):
        return True

    # ── Property licensing in a residential landlord context ───────────────────
    if re.search(r'property licens', t) and re.search(r'\blandlord', t):
        if not re.search(r'\b(commercial landlord|retail|commercial propert)\b', t):
            return True

    return False


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    os.makedirs('docs', exist_ok=True)

    # Load existing data
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE) as f:
            data = json.load(f)
        print(f"Loaded existing data: {len(data['questions'])} questions")
    else:
        data = {'lastUpdated': None, 'questions': []}
        print("No existing data — starting fresh")

    existing_ids = {q['id'] for q in data['questions']}

    # Date range: from last update minus 3-day overlap, or last 60 days if first run
    if data['lastUpdated']:
        from_date = (
            datetime.fromisoformat(data['lastUpdated']) - timedelta(days=3)
        ).strftime('%Y-%m-%d')
    else:
        from_date = (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d')
    to_date = datetime.now().strftime('%Y-%m-%d')

    print(f"Fetching questions from {from_date} to {to_date}...")

    # Fetch for each keyword
    seen = {}
    for i, kw in enumerate(KEYWORDS, 1):
        time.sleep(2)
        params = urlencode({
            'searchTerm': kw,
            'tabledWhenFrom': from_date,
            'tabledWhenTo': to_date,
            'take': 100,
            'skip': 0,
        })
        d = http_get(f'{QUESTIONS_API}?{params}')
        results = d.get('results', []) if d else []
        new_count = 0
        for r in results:
            qid = r['value']['id']
            if qid not in seen:
                seen[qid] = r['value']
                new_count += 1
        if results:
            print(f"  [{i}/{len(KEYWORDS)}] '{kw}': {len(results)} found, {new_count} new")

    # Filter: PRS-relevant and not already stored
    new_raw = {
        qid: q for qid, q in seen.items()
        if qid not in existing_ids and is_prs(q['questionText'])
    }
    print(f"\n{len(new_raw)} new PRS-relevant questions. Fetching member details...")

    # Fetch member details for new questions
    member_ids = list({q['askingMemberId'] for q in new_raw.values()})
    member_cache = {}
    for mid in member_ids:
        d = http_get(f'{MEMBERS_API}/{mid}', timeout=15)
        member_cache[mid] = d.get('value') if d else None
        time.sleep(0.5)

    # Build structured question records
    new_questions = []
    for q in new_raw.values():
        m = member_cache.get(q['askingMemberId'])
        po = m.get('latestParty') if m else None
        party = ''
        if po:
            party = PARTY_MAP.get(po.get('name', ''), po.get('abbreviation', '') or po.get('name', ''))
        mon = monday_of(q['dateTabled'])
        da = q.get('dateForAnswer', '')
        new_questions.append({
            'id':             q['id'],
            'memberName':     m['nameDisplayAs'] if m else f"Member {q['askingMemberId']}",
            'party':          party,
            'house':          q.get('house', ''),
            'question':       q.get('questionText', ''),
            'dateTabled':     fmt_date(q.get('dateTabled', '')),
            'dateTabledRaw':  q.get('dateTabled', '')[:10],
            'dateForAnswer':  f"Due {fmt_date(da)}" if da else '',
            'weekCommencing': mon.isoformat(),
            'url':            q_url(q.get('dateTabled', ''), q.get('uin', '')),
        })

    # Merge, trim to last 52 weeks, sort
    data['questions'].extend(new_questions)
    data['lastUpdated'] = datetime.now().isoformat()
    cutoff = (datetime.now() - timedelta(weeks=52)).strftime('%Y-%m-%d')
    data['questions'] = [
        q for q in data['questions'] if q.get('dateTabledRaw', '') >= cutoff
    ]
    data['questions'].sort(key=lambda q: q.get('dateTabledRaw', ''), reverse=True)

    with open(DATA_FILE, 'w') as f:
        json.dump(data, f, indent=2)

    print(f"\n✓ Added {len(new_questions)} new questions. Total stored: {len(data['questions'])}")


if __name__ == '__main__':
    main()
