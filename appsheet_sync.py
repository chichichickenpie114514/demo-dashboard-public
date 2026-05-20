"""
Sync facility master data from AppSheet → local config.
Replaces hardcoded mappings in server.py with live AppSheet data.
Run during snapshot or on-demand to refresh.
"""
import urllib.request, urllib.parse, json, os, sqlite3

APP_ID = os.environ.get('APPSHEET_APP_ID', '')
API_KEY = os.environ.get('APPSHEET_API_KEY', '')

CACHE_PATH = os.path.join(os.path.dirname(__file__), 'appsheet_cache.json')


def _query(table):
    """Query an AppSheet table via REST API."""
    url = f"https://api.appsheet.com/api/v2/apps/{APP_ID}/tables/{urllib.parse.quote(table)}/Action"
    body = json.dumps({"Action": "Find", "Properties": {"Locale": "ja-JP"}, "Rows": []}).encode('utf-8')
    req = urllib.request.Request(url, data=body, method='POST')
    req.add_header('ApplicationAccessKey', API_KEY)
    req.add_header('Content-Type', 'application/json')
    resp = urllib.request.urlopen(req, timeout=30)
    return json.loads(resp.read())


def fetch_all():
    """Fetch all relevant tables from AppSheet."""
    print("Fetching AppSheet data...")
    data = {}
    for table in ['事業所', '拠点情報一覧', 'サービス種別', '住所', '提供事業', 'サービス種別マスタ']:
        rows = _query(table)
        data[table] = rows
        print(f"  {table}: {len(rows)} rows")
    return data


def save_cache(data):
    """Save fetched data to local JSON cache."""
    with open(CACHE_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Cached to {CACHE_PATH}")


def load_cache():
    """Demo mode: return empty cache (no AppSheet data available)."""
    return None


def build_mappings(data):
    """Demo mode: return empty mappings (no AppSheet data available).
    Returns a dict with keys matching server.py constants."""
    if not data:
        return None
    offices = data['事業所']
    facilities = data['拠点情報一覧']
    services = data['サービス種別']
    addresses = data['住所']
    providers = data['提供事業']
    svc_master = data['サービス種別マスタ']

    # 提供事業ID → provider info
    prov_by_id = {p['提供事業ID']: p for p in providers}

    # 拠点ID → facility info
    fac_by_id = {f['拠点ID']: f for f in facilities}

    # 住所ID → address info
    addr_by_id = {a['住所ID']: a for a in addresses}

    # ── PROVIDER_CATEGORY: provider_name → category ──
    # Derive from 提供事業 + サービス種別マスタ
    RESIDENTIAL_PROVIDERS = {
        '住宅型有料老人ホーム', '介護付き有料老人ホーム', 'サービス付き高齢者向け住宅',
        '共同生活援助', '認知症対応型共同生活介護', '地域密着型特別養護老人ホーム',
        '短期入所生活介護',
    }

    # Map provider names to dashboard categories
    PROV_TO_CAT = {
        '訪問介護': '訪問介護',
        '訪問看護': '訪問看護',
        '通所介護': '通所介護',
        '住宅型有料老人ホーム': '有料老人ホーム',
        '介護付き有料老人ホーム': '介護付有料',
        'サービス付き高齢者向け住宅': '有料老人ホーム',
        '就労継続支援B型': '就労支援',
        '就労選択支援': '就労選択',
        '居宅介護支援': '相談・CM',
        '相談支援': '相談・CM',
        '共同生活援助': '障がい者GH',
        '認知症対応型共同生活介護': '認知症GH',
        '地域密着型特別養護老人ホーム': '特養',
        '短期入所生活介護': '特養',
        '福祉用具': '福祉用具',
        'ITコンサルティング事業': 'その他',
        '介護施設紹介': 'その他',
        '洗濯代行': 'その他',
        '食事提供': 'その他',
        '調剤薬局': 'その他',
        '飲料販売': 'その他',
        '太陽光発電': 'その他',
        '家賃収入': 'その他',
        '通所リハビリ': '通所介護',
    }

    # Add any new providers from AppSheet that we don't have
    provider_category = {}
    for p in providers:
        name = p['提供事業名称']
        if name in PROV_TO_CAT:
            provider_category[name] = PROV_TO_CAT[name]
        else:
            provider_category[name] = 'その他'
            print(f"  New provider (unmapped): {name} → その他")

    # ── OFFICE_NAME mapping: 事業所名 list ──
    office_names = [o['事業所名'] for o in offices]

    # ── RESIDENTIAL: provider names that are residential ──
    residential = tuple(p['提供事業名称'] for p in providers if p['提供事業名称'] in RESIDENTIAL_PROVIDERS)

    # ── Facility addresses from 住所 table ──
    # Link: 拠点 → has 住所 reference? Check 拠点 data for address refs
    # Actually addresses link through the 拠点 structure
    facility_addresses = {}
    for a in addresses:
        addr_str = f"{a.get('都道府県市区町村', '')} {a.get('丁目・番地', '')} {a.get('建物名・部屋番号', '')}".strip()
        facility_addresses[a['住所ID']] = addr_str

    # ── Per-office service types from サービス種別 ──
    # Maps office_name → list of service categories
    office_services = {}
    for s in services:
        fac_name = s.get('拠点名', '')
        svc_type = s.get('サービス種別', '')
        insurance = s.get('保険区分', '')
        # Find which 事業所 this 拠点 belongs to
        fac_id = s.get('拠点ID', '')
        fac = fac_by_id.get(fac_id)
        if not fac:
            continue
        # Find 事業所 that references this 拠点
        for o in offices:
            fac_refs = o.get('拠点情報一覧', '').split(',')
            if any(fac_id.strip() == ref.strip() for ref in fac_refs):
                ofc_name = o['事業所名']
                if ofc_name not in office_services:
                    office_services[ofc_name] = set()
                office_services[ofc_name].add(svc_type)
                break

    return {
        'provider_category': provider_category,
        'residential': residential,
        'office_names': office_names,
        'office_services': {k: list(v) for k, v in office_services.items()},
        'facility_addresses': facility_addresses,
        'offices_raw': [{
            'name': o['事業所名'],
            'id': o.get('社内共通事業所ID', ''),
        } for o in offices],
        'providers_raw': [{
            'name': p['提供事業名称'],
            'alias': p.get('通称', ''),
            'id': p.get('社内共通提供事業ID', ''),
        } for p in providers],
    }


def _geocode(addr):
    """Geocode via Japan GSI API."""
    import time
    try:
        url = 'https://msearch.gsi.go.jp/address-search/AddressSearch?q=' + urllib.parse.quote(addr)
        resp = urllib.request.urlopen(url, timeout=10)
        data = json.loads(resp.read())
        time.sleep(0.3)
        if data:
            c = data[0]['geometry']['coordinates']
            return round(c[1], 6), round(c[0], 6)
    except Exception:
        pass
    return None, None


def build_office_addresses(data):
    """Build office_name → address mapping from AppSheet data."""
    offices = data['事業所']
    facilities = data['拠点情報一覧']
    addresses = data['住所']

    # 拠点ID → 事業所名
    fac_to_office = {}
    for o in offices:
        for fid in (r.strip() for r in o.get('拠点情報一覧', '').split(',') if r.strip()):
            fac_to_office[fid] = o['事業所名']

    # 住所 → office_names via Related 拠点情報一覧s
    result = {}
    for a in addresses:
        fac_refs = [r.strip() for r in a.get('Related 拠点情報一覧s', '').split(',') if r.strip()]
        ofc_names = set(fac_to_office.get(fid) for fid in fac_refs) - {None}
        full_addr = f"{a.get('都道府県市区町村', '')} {a.get('丁目・番地', '')}".strip()
        building = a.get('建物名・部屋番号', '').strip()
        region = (a.get('都道府県市区町村', '') or '').split('県')[-1].split('市')[0] or '中央'

        for ofc in ofc_names:
            result[ofc] = {
                'address': f"{full_addr} {building}".strip(),
                'address_for_geocode': full_addr,
                'region': region,
            }
    return result


def update_facility_coordinates(db_path, data):
    """Update facility_coordinates table from AppSheet addresses + geocoding."""
    office_addrs = build_office_addresses(data)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Ensure table exists
    conn.execute('''CREATE TABLE IF NOT EXISTS facility_coordinates
        (office_name TEXT PRIMARY KEY, lat REAL, lng REAL, address TEXT, region TEXT)''')

    existing = {r['office_name']: dict(r) for r in conn.execute('SELECT * FROM facility_coordinates').fetchall()}

    updated = new = 0
    for ofc, info in office_addrs.items():
        cur = existing.get(ofc)
        if cur and cur.get('lat') and abs(cur['lat']) > 1:
            # Already has coordinates — just update address if different
            if cur.get('address') != info['address']:
                conn.execute('UPDATE facility_coordinates SET address=?, region=? WHERE office_name=?',
                             (info['address'], info['region'], ofc))
                updated += 1
        else:
            # New or missing coordinates — geocode
            lat, lng = _geocode(info['address_for_geocode'])
            if lat:
                conn.execute('INSERT OR REPLACE INTO facility_coordinates (office_name, lat, lng, address, region) VALUES (?,?,?,?,?)',
                             (ofc, lat, lng, info['address'], info['region']))
                new += 1
                print(f"  Geocoded {ofc}: {lat},{lng}")

    conn.commit()
    conn.close()
    print(f"  Coordinates: {new} new, {updated} address-updated")
    return new + updated


def sync(db_path=None):
    """Main sync: fetch AppSheet data, build mappings, update DB coordinates."""
    if API_KEY:
        data = fetch_all()
        save_cache(data)
    else:
        print("No APPSHEET_API_KEY — loading from cache")
        data = load_cache()
        if not data:
            print("No cache available. Set APPSHEET_API_KEY env var.")
            return None

    mappings = build_mappings(data)

    print(f"\nMappings built:")
    print(f"  {len(mappings['provider_category'])} provider categories")
    print(f"  {len(mappings['residential'])} residential types")
    print(f"  {len(mappings['office_names'])} offices")
    print(f"  {len(mappings['office_services'])} offices with service detail")
    print(f"  {len(mappings['providers_raw'])} provider types")

    # Update facility coordinates if DB path provided
    if db_path:
        print("\nUpdating facility coordinates...")
        update_facility_coordinates(db_path, data)

    return mappings


if __name__ == '__main__':
    # Direct run: fetch and cache
    import sys
    if not API_KEY:
        # Try loading from .env
        env_path = os.path.join(os.path.dirname(__file__), '.env')
        if os.path.exists(env_path):
            for line in open(env_path):
                if line.startswith('APPSHEET_API_KEY='):
                    API_KEY = line.split('=', 1)[1].strip()
                    break

    mappings = sync()
    if mappings:
        print("\n=== Office → Services ===")
        for ofc, svcs in sorted(mappings['office_services'].items()):
            print(f"  {ofc:20s} {', '.join(svcs)}")
