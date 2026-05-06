#!/usr/bin/env python3
"""
Parliament PRS Monitor — Daily updater
Fetches written questions about the private residential rented sector
and saves them to docs/data.json for the web dashboard.
"""

import json, os, re, subprocess, time
from datetime import datetime, timedelta
from urllib.parse import urlencode

DATA_FILE = os.path.join('docs', 'data.json')
QUESTIONS_API = 'https://questions-statements-api.parliament.uk/api/writtenquestions/questions'
MEMBERS_API   = 'https://members-api.parliament.uk/api/Members'

KEYWORDS = [
    'landlord', 'section 21', 'renters rights', 'rent repayment order',
    'tenancy deposit', 'assured shorthold', 'private rented', 'privately rented',
    'private renting', 'rented properties', 'rented homes', 'letting agent',
    'landlord licensing', 'rent control', 'rent stabilisation', 'rent inflation',
    'rent determination', 'build-to-rent', 'rental accommodation', 'rental housing',
    'leasehold', 'leaseholder', 'ground rent', 'commonhold', 'enfranchisement',
    'lha freeze', 'lha rates', 'housing allowance freeze', 'housing benefit landlord',
    'prs database', 'prs ombudsman', 'rogue landlord', 'decent homes',
    'section 13', 'energy performance certificate', 'minimum energy efficiency',
    'warm home', 'energy company obligation', 'eco4', 'energy efficiency',
    'energy performance', 'retrofit', 'making tax digital', 'landlord registration',
    'property licensing', 'personal independence payment',
    'estate management charge', 'estate rent charge',
    'tenant displacement', 'blight notice landlord',
    'local housing allowance',
]

PARTY_MAP = {
    'Labour':'Lab','Conservative':'Con','Liberal Democrat':'LD',
    'Scottish National Party':'SNP','Crossbench':'CB','Independent':'Ind',
    'Plaid Cymru':'PC','Reform UK':'Ref','Green Party':'Green',
    'Democratic Unionist Party':'DUP','Non-affiliated':'Non-aff',
    'Alliance Party':'Alliance','Ulster Unionist Party':'UUP',
    'Social Democratic and Labour Party':'SDLP',
}

def http_get(url, timeout=25):
    r = subprocess.run(
        ['curl','-s',f'--max-time',str(timeout),'-H','User-Agent: PRS-Monitor/1.0',url],
        capture_output=True, text=True)
    try: return json.loads(r.stdout) if r.stdout.strip() else None
    except: return None

def ordinal(n):
    s=['th','st','nd','rd']; v=n%100
    return f"{n}{s[(v-20)%10] if (v-20)%10<4 else s[v] if v<4 else s[0]}"

def fmt_date(iso):
    if not iso: return ''
    d=datetime.fromisoformat(iso[:19])
    return f"{ordinal(d.day)} {d.strftime('%B')}"

def monday_of(iso):
    d=datetime.fromisoformat(iso[:19]).date()
    return d-timedelta(days=d.weekday())

def q_url(dt,uin):
    return f"https://questions-statements.parliament.uk/written-questions/detail/{dt[:10]}/{uin}"

# ── PRS relevance filter ───────────────────────────────────────────────────────

def is_prs(text):
    t = text.lower()

    # ── HARD EXCLUSIONS — checked first, always trump inclusions ──────────────

    # Social rented sector (distinct from private rented)
    if re.search(r'\bsocial rented\b', t):
        return False

    # Park homes — not PRS
    if re.search(r'\bpark home', t):
        return False

    # Commercial / non-residential (only exclude when no residential landlord context)
    has_residential = bool(re.search(
        r'\b(landlord|tenant|tenancy|leaseholder|private rent|privately rent|'
        r'rented|rental|renters?|letting)\b', t))
    if not has_residential:
        if re.search(r'\b(commercial tenant|commercial landlord|commercial rental|'
                     r'commercial lease|commercial property|commercial premises|'
                     r'industrial unit|retail premises|business premises|'
                     r'farm business tenancy|agricultural tenancy)\b', t):
            return False

    # ── STRONG DIRECT SIGNALS — any one is sufficient to include ──────────────
    strong = [
        r'private rented sector', r'private renter', r'private rental',
        r'private landlord', r'private tenant', r'private renting', r'privately rented',
        r'rented sector', r'rental sector',
        r'renters.{0,5}rights act', r'renters\b',
        r'rent repayment', r'\bsection 21\b', r'assured shorthold', r'tenancy deposit',
        r'buy.to.let', r'build.to.rent', r'\bhmo\b', r'house in multiple occupation',
        r'local housing allowance', r'\blha\b',
        r'lha (rate|level|freeze|cap)', r'housing allowance (freeze|rate|level)',
        r'ground rent', r'leasehold reform', r'leasehold and commonhold', r'commonhold',
        r'leasehold enfranchis', r'leaseholder', r'leasehold house', r'leasehold flat',
        r'right to manage', r'managing agent', r'right to rent', r'letting agent',
        r'landlord licens', r'landlord registr', r'landlord database',
        r'institutional landlord', r'no.fault eviction', r'section 21 eviction',
        r'pre.emptive eviction', r'rent appeal', r'property chamber',
        r'section 13 rent', r'section 13 appeal', r'section 13 determin',
        r'rent determin', r'market rent determin',
        r'rent control', r'rent stabilisation', r'rent inflation', r'rent freeze', r'rent cap',
        r'decent homes standard', r'decent homes',
        r'prs database', r'prs ombudsman', r'landlord ombudsman',
        r'awaab', r'rogue landlord',
        r'civil penalt.{0,20}landlord', r'landlord.{0,20}civil penalt',
        r'spray foam insulation', r'guarantor.{0,20}(rent|tenancy)',
        r'rental income.{0,20}(tax|hmrc)', r'licensed accommodation',
        r'rented propert', r'rented home', r'rental housing', r'rental accommodation',
        r'rental market', r'private rent inflation',
        r'multifamily.{0,30}(rent|housing|sector)',
        # Leasehold reform specifics
        r'enfranchisement', r'collective enfranchisement',
        r'leasehold and freehold reform act', r'leasehold and commonhold reform',
        r'commonhold and leasehold reform', r'estate management charge',
        r'estate rent charge', r'deferment.{0,20}capitalisation',
        r'capitalisation rate', r'marriage value.{0,20}leas',
        r'landlord and tenant act', r'section 20b',
        # Building safety for leaseholders
        r'building safety act.{0,80}leaseholder',
        r'leaseholder.{0,80}building safety act',
        r'section 24 building manager.{0,80}(leasehold|commonhold)',
        # Tenant displacement & tenant rights
        r'tenant displacement', r'repeated displacement',
        r'forced moves.{0,20}tenant',
        # Landlord specifics
        r'landlord.supplied', r'blight notice.{0,20}landlord',
        r'landlord.{0,30}blight notice',
        r'vacant residential.{0,30}landlord', r'landlord.{0,30}vacant residential',
        # MEES / minimum standards — explicit enough to always include
        r'minimum energy efficiency standard', r'\bmees\b',
    ]
    for p in strong:
        if re.search(p, t): return True

    # ── CONTEXTUAL: landlord + residential housing ─────────────────────────────
    # Note: 'propert' without trailing \b so it matches 'property', 'properties' etc.
    if re.search(r'\blandlord\b', t) and re.search(
        r'\b(tenant|tenancy|rented|possession|evict|rent|letting|dwelling|home|flat|house|propert)', t):
        return True

    # ── ENERGY EFFICIENCY SCHEME OVERSIGHT + HOMES ────────────────────────────
    # Catches questions about oversight/redress/quality of energy scheme installations
    # that affect homeowners and tenants (Liz Jarvis, Dave Doogan type questions)
    if re.search(r'\b(energy efficiency|insulation|retrofit)\b', t) and re.search(
        r'\b(oversight|accountability|redress|corrective|defective|improperly|scheme)\b', t):
        if re.search(r'\b(home|household|housing|domestic|building|publicly funded)\b', t):
            if not re.search(r'\b(commercial|industrial|park home)\b', t):
                return True

    # ── WARM HOMES PLAN CONSULTATION / GOVERNANCE ─────────────────────────────
    # Catches Warm Homes Plan governance/consultation questions (Liz Saville-Roberts)
    if re.search(r'warm homes plan', t) and re.search(
        r'\b(consultation|oversight|governance|agency|accountability|microgeneration)\b', t):
        return True

    # ── MAKING TAX DIGITAL + LANDLORD ─────────────────────────────────────────
    if re.search(r'making tax digital', t) and re.search(r'\blandlord', t):
        return True

    # ── PROPERTY LICENSING + RESIDENTIAL LANDLORD ────────────────────────────
    if re.search(r'property licens', t) and re.search(r'\blandlord', t):
        if not re.search(r'\b(commercial landlord|retail|commercial propert)\b', t):
            return True

    # ── PIP / DISABILITY BENEFITS + RENT ──────────────────────────────────────
    if re.search(r'personal independence payment', t) and re.search(
        r'\b(rent|housing cost|housing benefit)\b', t):
        return True

    # ── EPC / ENERGY EFFICIENCY — tightly controlled ──────────────────────────
    # Rule: only include if there is ALSO an explicit private renting/landlord signal
    # OR the question is specifically about MEES exemptions (affects landlords)
    prs_energy_signal = (
        r'\b(private rent|privately rent|private landlord|landlord|tenant|'
        r'rented home|rented propert|rented sector|rental propert)\b'
    )
    if re.search(r'\b(energy performance certificate|epc)\b', t):
        if re.search(prs_energy_signal, t):
            return True
        # EPC methodology/valuation questions affect rental compliance broadly
        if re.search(r'\b(valuation|methodology|standard|rating|band|compliance)', t):
            return True

    if re.search(r'\b(energy efficiency|retrofit|insulation)\b', t):
        if re.search(prs_energy_signal, t):
            return True
        # Also include questions specifically about residential housing stock
        # (e.g. rural homes, solid wall, older housing stock) but NOT programme admin
        if re.search(r'\b(rural|solid wall|housing stock|older hous)\b', t):
            if not re.search(
                r'\b(workforce|supply chain|redundanc|job|funding allocation|'
                r'delivery mechanism|procurement|installer|underspend|'
                r'geothermal|community energy|chinese import|park home|'
                r'commercial|industrial|high.street|town.centre|vat)\b', t):
                return True

    # ── WARM HOMES PLAN / ECO4 — only with explicit PRS/landlord signal ───────
    if re.search(r'\b(warm homes plan|warm homes local grant|warm homes social fund|'
                 r'eco4|energy company obligation|great british insulation)\b', t):
        if re.search(prs_energy_signal, t):
            return True

    # ── WARM HOMES DISCOUNT — only with tenant/private renting context ─────────
    if re.search(r'\bwarm home.{0,10}discount\b', t):
        if re.search(r'\b(private rent|rented|tenant|landlord)\b', t):
            return True

    # ── UC / HOUSING BENEFIT — only tied to private renting (not social) ──────
    if re.search(r'(universal credit|housing benefit|housing element).{0,80}'
                 r'(private landlord|private rent)', t): return True
    if re.search(r'housing (benefit|element).{0,60}(private|rented|rental|tenant)', t):
        return True
    if re.search(r'(universal credit|housing benefit|housing element).{0,80}landlord', t):
        if not re.search(r'\b(social rented|social housing|housing association)\b', t):
            return True

    # ── RESIDENTIAL LEASEHOLD ──────────────────────────────────────────────────
    if re.search(r'\bleasehold\b', t):
        if re.search(
            r'\b(leaseholder|ground rent|service charge|residential|flat|commonhold|'
            r'enfranchis|reform bill|freehold reform|managing agent|right to manage|'
            r'estate management|estate rent charge)\b', t):
            return True

    # ── LHA / HOUSING ALLOWANCE ────────────────────────────────────────────────
    if re.search(r'housing allowance', t) and re.search(
        r'\b(rent|rented|private|tenant|poverty|homelessness)\b', t):
        return True

    # ── STANDARDS OF RENTED PROPERTIES ────────────────────────────────────────
    if re.search(r'\b(standard|condition|quality).{0,30}rented\b', t):
        return True
    if re.search(r'\brented\b.{0,30}\b(standard|condition|quality|propert|home|sector)\b', t):
        return True

    return False


def main():
    os.makedirs('docs', exist_ok=True)
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE) as f: data = json.load(f)
        print(f"Loaded {len(data['questions'])} existing questions")
    else:
        data = {'lastUpdated': None, 'questions': []}
        print("Starting fresh")

    existing_ids = {q['id'] for q in data['questions']}

    if data['lastUpdated']:
        from_date = (datetime.fromisoformat(data['lastUpdated']) - timedelta(days=3)).strftime('%Y-%m-%d')
    else:
        from_date = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
    to_date = datetime.now().strftime('%Y-%m-%d')
    print(f"Fetching {from_date} → {to_date}")

    seen = {}
    for i, kw in enumerate(KEYWORDS, 1):
        time.sleep(2)
        skip = 0
        while True:
            params = urlencode({'searchTerm':kw,'tabledWhenFrom':from_date,
                                'tabledWhenTo':to_date,'take':100,'skip':skip})
            d = http_get(f'{QUESTIONS_API}?{params}')
            if not d: break
            results = d.get('results', [])
            new = sum(1 for r in results if r['value']['id'] not in seen)
            for r in results:
                if r['value']['id'] not in seen:
                    seen[r['value']['id']] = r['value']
            total = d.get('totalResults', 0)
            if results and new > 0:
                print(f"  [{i}/{len(KEYWORDS)}] '{kw}' +{new} (total: {len(seen)})")
            skip += 100
            if not results or skip >= total: break
            time.sleep(1)

    new_raw = {qid: q for qid, q in seen.items()
               if qid not in existing_ids and is_prs(q['questionText'])}
    print(f"\n{len(new_raw)} new PRS questions. Fetching members...")

    member_ids = list({q['askingMemberId'] for q in new_raw.values()})
    member_cache = {}
    for mid in member_ids:
        d = http_get(f'{MEMBERS_API}/{mid}', timeout=15)
        member_cache[mid] = d.get('value') if d else None
        time.sleep(0.5)

    new_questions = []
    for q in new_raw.values():
        m = member_cache.get(q['askingMemberId'])
        po = m.get('latestParty') if m else None
        party = ''
        if po: party = PARTY_MAP.get(po.get('name',''), po.get('abbreviation','') or po.get('name',''))
        mon = monday_of(q['dateTabled'])
        da  = q.get('dateForAnswer','')
        new_questions.append({
            'id':             q['id'],
            'memberName':     m['nameDisplayAs'] if m else f"Member {q['askingMemberId']}",
            'party':          party,
            'house':          q.get('house',''),
            'question':       q.get('questionText',''),
            'dateTabled':     fmt_date(q.get('dateTabled','')),
            'dateTabledRaw':  q.get('dateTabled','')[:10],
            'dateForAnswer':  f"Due {fmt_date(da)}" if da else '',
            'weekCommencing': mon.isoformat(),
            'url':            q_url(q.get('dateTabled',''), q.get('uin','')),
        })

    data['questions'].extend(new_questions)
    data['lastUpdated'] = datetime.now().isoformat()
    cutoff = (datetime.now() - timedelta(weeks=52)).strftime('%Y-%m-%d')
    data['questions'] = [q for q in data['questions'] if q.get('dateTabledRaw','') >= cutoff]
    data['questions'].sort(key=lambda q: q.get('dateTabledRaw',''), reverse=True)

    with open(DATA_FILE,'w') as f: json.dump(data, f, indent=2)
    print(f"\n✓ Added {len(new_questions)} questions. Total: {len(data['questions'])}")

if __name__ == '__main__':
    main()
