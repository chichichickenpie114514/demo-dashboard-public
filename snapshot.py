"""
Snapshot sales-common-format MySQL DB into local SQLite for dashboard use.
Read-only: never writes back to the source DB.
"""
import sqlite3
import json
import os
import sys
import mysql.connector

# Force unbuffered output (critical for Cloud Run log visibility)
sys.stdout.reconfigure(line_buffering=True)

# Limit months to process for testing (0 = all)
MAX_MONTHS = int(os.environ.get('MAX_MONTHS', '0'))

_mysql_host = os.environ.get('MYSQL_HOST', '')
MYSQL = dict(
    user=os.environ.get('MYSQL_USER', ''),
    password=os.environ.get('MYSQL_PASSWORD', ''),
    database=os.environ.get('MYSQL_DATABASE', ''),
    connection_timeout=30,
)
# Cloud SQL Auth Proxy uses unix socket; direct connection uses TCP host
if _mysql_host.startswith('/'):
    MYSQL['unix_socket'] = _mysql_host
else:
    MYSQL['host'] = _mysql_host

SQLITE_PATH = os.environ.get('SQLITE_PATH', os.path.join(os.path.dirname(__file__), 'sales.db'))

TABLES = [
    "sales_summary",
    "sales_fmt_data",  # accounting-journal source for the 拠点（カード） tab
]

def get_create_sql(cursor, table):
    cursor.execute(f"SHOW CREATE TABLE `{table}`")
    row = cursor.fetchone()
    return row[1]

def mysql_to_sqlite_type(col_type):
    t = col_type.lower()
    if "int" in t:
        return "INTEGER"
    if any(x in t for x in ["decimal", "float", "double"]):
        return "REAL"
    if "datetime" in t or "date" in t or "time" in t:
        return "TEXT"
    if "json" in t:
        return "TEXT"
    return "TEXT"

def snapshot_table(mysql_cur, sqlite_cur, table):
    # Get column info
    mysql_cur.execute(f"DESCRIBE `{table}`")
    columns = mysql_cur.fetchall()
    col_names = [c[0] for c in columns]
    col_types = [mysql_to_sqlite_type(c[1]) for c in columns]

    # Create SQLite table
    col_defs = ", ".join(f'"{n}" {t}' for n, t in zip(col_names, col_types))
    sqlite_cur.execute(f'DROP TABLE IF EXISTS "{table}"')
    sqlite_cur.execute(f'CREATE TABLE "{table}" ({col_defs})')

    # Fetch and insert data
    mysql_cur.execute(f"SELECT * FROM `{table}`")
    rows = mysql_cur.fetchall()

    placeholders = ", ".join("?" * len(col_names))
    insert_sql = f'INSERT INTO "{table}" VALUES ({placeholders})'

    def convert(v):
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return v
        return str(v)

    converted = [tuple(convert(v) for v in row) for row in rows]
    sqlite_cur.executemany(insert_sql, converted)
    print(f"  {table}: {len(rows)} rows")


def main():
    # Sync AppSheet master data first (if API key available)
    try:
        import appsheet_sync
        if appsheet_sync.API_KEY or os.environ.get('APPSHEET_API_KEY'):
            appsheet_sync.API_KEY = os.environ.get('APPSHEET_API_KEY', appsheet_sync.API_KEY)
            appsheet_sync.sync()  # fetches data + builds cache (no db_path yet)
            print("AppSheet sync complete")
        else:
            print("No APPSHEET_API_KEY — using cached AppSheet data")
    except Exception as e:
        print(f"AppSheet sync skipped: {e}")

    print("Connecting to MySQL...")
    mysql_conn = mysql.connector.connect(**MYSQL)
    mysql_cur = mysql_conn.cursor()

    print(f"Writing to {SQLITE_PATH}")
    sqlite_conn = sqlite3.connect(SQLITE_PATH)
    sqlite_cur = sqlite_conn.cursor()
    # Optimize for bulk writes in a throwaway container — no crash safety needed
    sqlite_cur.execute("PRAGMA journal_mode = MEMORY")
    sqlite_cur.execute("PRAGMA synchronous = OFF")
    sqlite_cur.execute("PRAGMA cache_size = -64000")  # 64MB page cache

    for table in TABLES:
        print(f"Snapshotting {table}...")
        snapshot_table(mysql_cur, sqlite_cur, table)

    # Phase D: every distinct base_name gets its own office_name + area
    # via display_for_base_name. No hardcoded dict to maintain. New base_names
    # added to MySQL upstream flow through automatically on the next snapshot.
    print("Adding office_name, service_office, area to sales_summary...")
    from config import display_for_base_name, is_residential_sales_type

    sqlite_cur.execute('ALTER TABLE sales_summary ADD COLUMN office_name TEXT')
    sqlite_cur.execute('ALTER TABLE sales_summary ADD COLUMN area TEXT')

    # Resolve display/area per distinct base_name, then bulk-update.
    distinct_bns = [r[0] for r in sqlite_cur.execute(
        'SELECT DISTINCT base_name FROM sales_summary WHERE base_name IS NOT NULL'
    )]
    for bn in distinct_bns:
        display, area = display_for_base_name(bn)
        sqlite_cur.execute(
            'UPDATE sales_summary SET office_name = ?, area = ? WHERE base_name = ?',
            (display, area, bn)
        )

    # service_office = office_name (Phase D: one row per base_name, no further
    # re-attribution). BASE_SALES_TYPE_ROUTE still applies for split-billing
    # bases (e.g. 0048_うらら拠点 → うらら1 / うらら2 by sales_type keyword).
    sqlite_cur.execute('ALTER TABLE sales_summary ADD COLUMN service_office TEXT')
    sqlite_cur.execute('UPDATE sales_summary SET service_office = office_name')

    from config import BASE_SALES_TYPE_ROUTE
    for base, rules in BASE_SALES_TYPE_ROUTE.items():
        for keyword, ofc in rules:
            sqlite_cur.execute(
                'UPDATE sales_summary SET service_office = ? '
                'WHERE base_name = ? AND sales_type LIKE ?',
                (ofc, base, f'%{keyword}%')
            )

    filled = sqlite_cur.execute('SELECT COUNT(*) FROM sales_summary WHERE service_office IS NOT NULL').fetchone()[0]
    total_rows = sqlite_cur.execute('SELECT COUNT(*) FROM sales_summary').fetchone()[0]
    print(f"  {filled}/{total_rows} rows mapped across {len(distinct_bns)} distinct base_names")

    # is_residential flag + category: pre-computed from sales_type via NFKC
    # normalized matching. Absorbs upstream punctuation/whitespace drift at
    # snapshot time so API queries stay simple: WHERE is_residential = 1 or
    # GROUP BY category.
    print("Flagging residential sales_types (is_residential + category)...")
    from config import categorize_service
    sqlite_cur.execute('ALTER TABLE sales_summary ADD COLUMN is_residential INTEGER DEFAULT 0')
    sqlite_cur.execute('ALTER TABLE sales_summary ADD COLUMN category TEXT')
    distinct_stypes = [r[0] for r in sqlite_cur.execute(
        'SELECT DISTINCT sales_type FROM sales_summary WHERE sales_type IS NOT NULL'
    ).fetchall()]
    # Populate category for every sales_type
    cat_map = [(st, categorize_service(st)) for st in distinct_stypes if st]
    sqlite_cur.execute('CREATE TEMP TABLE _stype_cat (sales_type TEXT PRIMARY KEY, category TEXT)')
    sqlite_cur.executemany('INSERT INTO _stype_cat VALUES (?,?)', cat_map)
    sqlite_cur.execute('''
        UPDATE sales_summary SET category = (
            SELECT category FROM _stype_cat WHERE _stype_cat.sales_type = sales_summary.sales_type
        )
    ''')
    residential_stypes = [st for st in distinct_stypes if is_residential_sales_type(st)]
    if residential_stypes:
        st_ph = ','.join('?' * len(residential_stypes))
        sqlite_cur.execute(
            f'UPDATE sales_summary SET is_residential = 1 WHERE sales_type IN ({st_ph})',
            residential_stypes,
        )
    n_flagged = sqlite_cur.execute(
        'SELECT COUNT(DISTINCT sales_type) FROM sales_summary WHERE is_residential = 1'
    ).fetchone()[0]
    print(f"  {n_flagged} sales_types flagged residential: {sorted(residential_stypes)}")

    # Pre-compute person_id → latest name lookup (replaces customer_master.full_name).
    # Resolving on-the-fly in API queries would scan all sales_summary per request.
    print("Building _name_lookup (person_id → latest name)...")
    sqlite_cur.execute('DROP TABLE IF EXISTS _name_lookup')
    sqlite_cur.execute('''
        CREATE TABLE _name_lookup AS
        SELECT person_id, name FROM (
            SELECT person_id, name, service_month,
                   ROW_NUMBER() OVER (PARTITION BY person_id ORDER BY service_month DESC) AS rn
            FROM sales_summary
            WHERE name IS NOT NULL AND name != ''
        ) WHERE rn = 1
    ''')
    name_rows = sqlite_cur.execute('SELECT COUNT(*) FROM _name_lookup').fetchone()[0]
    print(f"  _name_lookup: {name_rows} entries")

    # Create indexes for dashboard query performance
    print("Creating indexes...")
    for sql in [
        'CREATE INDEX IF NOT EXISTS idx_ss_month ON sales_summary(service_month)',
        'CREATE INDEX IF NOT EXISTS idx_ss_person ON sales_summary(person_id)',
        'CREATE INDEX IF NOT EXISTS idx_ss_month_person ON sales_summary(service_month, person_id)',
        'CREATE INDEX IF NOT EXISTS idx_ss_month_person_type ON sales_summary(service_month, person_id, sales_type)',
        'CREATE INDEX IF NOT EXISTS idx_ss_month_person_debit ON sales_summary(service_month, person_id, debit_account)',
        'CREATE INDEX IF NOT EXISTS idx_ss_service_office ON sales_summary(service_office)',
        'CREATE INDEX IF NOT EXISTS idx_ss_month_residential ON sales_summary(service_month, is_residential)',
        'CREATE UNIQUE INDEX IF NOT EXISTS idx_name_lookup_person ON _name_lookup(person_id)',
        'CREATE INDEX IF NOT EXISTS idx_name_lookup_name ON _name_lookup(name)',
    ]:
        sqlite_cur.execute(sql)

    sqlite_conn.commit()

    # Create facility_coordinates from AppSheet data (not in MySQL)
    try:
        import appsheet_sync
        cache = appsheet_sync.load_cache()
        if cache:
            appsheet_sync.update_facility_coordinates(SQLITE_PATH, cache)
            print("facility_coordinates updated from AppSheet cache")
    except Exception as e:
        print(f"facility_coordinates skipped: {e}")

    sqlite_conn.close()
    mysql_cur.close()
    mysql_conn.close()
    print("Done.")

def generate_api_json(sqlite_path=SQLITE_PATH, output_dir=None):
    """
    Pre-compute all API responses as static JSON files.
    Uses Flask test client against the freshly built SQLite.
    """
    if output_dir is None:
        output_dir = os.path.join(os.path.dirname(__file__), 'data')
    os.makedirs(output_dir, exist_ok=True)

    # Use the SQLite-backed server (server_snapshot.py) for test client queries
    import server_snapshot
    server_snapshot.DB = sqlite_path

    # ── Best-effort: load 配賦 (haifu) matrix from Google Sheets ──
    # Failures here MUST NOT crash the snapshot — standard sales JSON files
    # take priority. Empty allocation just means /api/haifu shows native-only
    # cards.
    try:
        import haifu_sync
        haifu_raw = haifu_sync.fetch()
        haifu_records = haifu_sync.parse(haifu_raw)
        print(f"haifu_sync: fetched {len(haifu_raw)} sheet rows → {len(haifu_records)} allocation records")
    except Exception as e:
        print(f"haifu_sync FAILED — continuing with empty allocation: {type(e).__name__}: {e}")
        haifu_records = []
    server_snapshot.app.config['HAIFU_RECORDS'] = haifu_records

    # ── Best-effort: load management-accounting manual journal (xlsx) ──
    # Same fault-isolation policy: any failure here leaves the rest of the
    # snapshot untouched. Empty list just means /api/mgmt-journal returns
    # nothing and the 拠点（配賦2）tab degrades to the same data as 拠点（配賦）.
    try:
        import manage_jrn_sync
        mgmt_raw = manage_jrn_sync.fetch()
        mgmt_records = manage_jrn_sync.parse(mgmt_raw)
        print(f'manage_jrn_sync: fetched {len(mgmt_raw)} xlsx rows → {len(mgmt_records)} matching records')
    except Exception as e:
        print(f'manage_jrn_sync FAILED — continuing with empty journal: {type(e).__name__}: {e}')
        mgmt_records = []
    server_snapshot.app.config['MGMT_JRN_RECORDS'] = mgmt_records

    client = server_snapshot.app.test_client()

    def auth_get(path):
        """GET with fake session to bypass login_required."""
        with client.session_transaction() as sess:
            sess['user'] = {'email': 'snapshot@demo.local', 'name': 'Snapshot'}
        resp = client.get(path)
        if resp.status_code != 200:
            print(f"  WARN: {path} returned {resp.status_code}")
            return None
        return resp.get_json()

    def write_json(dirpath, filename, data):
        os.makedirs(dirpath, exist_ok=True)
        path = os.path.join(dirpath, filename)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
        size_kb = os.path.getsize(path) / 1024
        print(f"  {os.path.relpath(path, output_dir)} ({size_kb:.0f} KB)")

    # ── Global endpoints (no month param) ──
    print("\nGenerating global JSON files...")
    months_data = auth_get('/api/months')
    if not months_data:
        print("ERROR: /api/months returned no data. Aborting.")
        return
    write_json(output_dir, 'months.json', months_data)

    trend_data = auth_get('/api/trend-history')
    if trend_data:
        write_json(output_dir, 'trend_history.json', trend_data)

    # ── Per-month endpoints ──
    if MAX_MONTHS > 0:
        months_data = months_data[:MAX_MONTHS]
        print(f"TEST MODE: processing only {MAX_MONTHS} month(s)")
    for m_info in months_data:
        month = m_info['month']
        month_dir = os.path.join(output_dir, month)
        print(f"\nGenerating {month}...")

        # Simple month-parameterized endpoints
        endpoints = {
            'kpi.json':                   f'/api/kpi?month={month}',
            'facilities_billing.json':    f'/api/facilities?month={month}&view=billing',
            'services.json':              f'/api/services?month={month}',
            'persons.json':               f'/api/persons?month={month}&limit=0',
            'analysis.json':              f'/api/analysis?month={month}',
            'cross_sell.json':            f'/api/cross-sell?month={month}',
            'map.json':                   f'/api/map?month={month}',
            'alerts.json':                f'/api/alerts?month={month}',
            'facility_cards.json':        f'/api/facility-cards?month={month}',
            'haifu.json':                 f'/api/haifu?month={month}',
            'mgmt_journal.json':          f'/api/mgmt-journal?month={month}',
        }
        for filename, url in endpoints.items():
            data = auth_get(url)
            if data is not None:
                write_json(month_dir, filename, data)

        # ── Card-view facility details (sales_fmt_data source) ──
        # Bundle per-facility detail for the new card-view tab. Keyed by
        # canonical facility name (same shape as facility_cards.json names).
        cards = auth_get(f'/api/facility-cards?month={month}') or []
        if cards:
            print(f"  Bundling facility card details ({len(cards)} facilities)...")
            card_names = [c.get('name') for c in cards if c.get('name')]
            all_card_details = {}
            for n in card_names:
                d = auth_get(f'/api/facility-cards-detail?month={month}&name={n}')
                if d:
                    all_card_details[n] = d
            write_json(month_dir, 'facility_cards_detail.json', all_card_details)

        # ── Entity bundles ──
        print(f"  [CHECKPOINT] Starting entity bundles for {month}")

        # Facility details + trends: keyed by facility name (billing view).
        fac_billing = auth_get(f'/api/facilities?month={month}&view=billing') or []
        names = []
        seen = set()
        for fac in fac_billing:
            n = fac.get('name', '')
            if n and n not in seen:
                seen.add(n); names.append(n)

        if names:
            # Flat dict {name: detail}. server.py still supports the legacy
            # nested {billing, resident} shape for backward compatibility with
            # older snapshots that haven't been regenerated yet.
            print(f"  Bundling facility details ({len(names)} facilities)...")
            all_fac_details = {}
            for n in names:
                d = auth_get(f'/api/facility-detail?month={month}&name={n}&view=billing')
                if d:
                    all_fac_details[n] = d
            print(f"  [CHECKPOINT] Facility details done: {len(all_fac_details)} facilities")
            write_json(month_dir, 'facility_details.json', all_fac_details)

            print(f"  Bundling facility trends...")
            all_fac_trends = {}
            for n in names:
                trend = auth_get(f'/api/facility-trend?name={n}')
                if trend:
                    all_fac_trends[n] = trend
            print(f"  [CHECKPOINT] Facility trends done")
            write_json(month_dir, 'facility_trends.json', all_fac_trends)

        # Person details: keyed by person_id
        # Fetch full person list (no limit) then batch detail calls
        print(f"  [CHECKPOINT] Fetching full person list...")
        persons = auth_get(f'/api/persons?month={month}&limit=0')
        if persons:
            print(f"  Bundling person details ({len(persons)} persons)...")
            all_person_details = {}
            for i, p in enumerate(persons):
                aid = p.get('person_id', '')
                if not aid:
                    continue
                detail = auth_get(f'/api/person-detail?id={aid}&month={month}')
                if detail:
                    all_person_details[str(aid)] = detail
                if (i + 1) % 100 == 0:
                    print(f"    ...{i+1}/{len(persons)}")
            print(f"  [CHECKPOINT] Person details done: {len(all_person_details)} persons")
            write_json(month_dir, 'person_details.json', all_person_details)

    print(f"\nJSON generation complete. Output: {output_dir}")


GCS_BUCKET = os.environ.get('GCS_BUCKET', 'demo-dashboard-data')

def upload_to_gcs(local_dir=None, bucket_name=None):
    """Upload all JSON files from local_dir to GCS bucket.
    Uses google-cloud-storage SDK if ADC available, falls back to gcloud CLI."""
    import subprocess
    if local_dir is None:
        local_dir = os.path.join(os.path.dirname(__file__), 'data')
    if bucket_name is None:
        bucket_name = GCS_BUCKET
    gs_url = f'gs://{bucket_name}/'

    # Try SDK first (works on Cloud Run with service account)
    try:
        from google.cloud import storage
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        count = 0
        for root, dirs, files in os.walk(local_dir):
            for f in files:
                if not f.endswith('.json'):
                    continue
                local_path = os.path.join(root, f)
                blob_path = os.path.relpath(local_path, local_dir)
                blob = bucket.blob(blob_path)
                blob.upload_from_filename(local_path, content_type='application/json')
                count += 1
        print(f"Uploaded {count} files to {gs_url} (SDK)")
        return
    except Exception:
        pass

    # Fallback: gcloud CLI (works locally with gcloud auth)
    result = subprocess.run(
        ['gcloud', 'storage', 'cp', '--recursive', local_dir + '/*', gs_url],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print(f"Uploaded to {gs_url} (gcloud CLI)")
    else:
        print(f"Upload failed: {result.stderr}")


if __name__ == "__main__":
    import time as _time
    _t0 = _time.time()
    print("[STAGE 1/3] Building SQLite from MySQL...")
    main()
    print(f"[STAGE 1/3] SQLite done in {_time.time()-_t0:.0f}s")
    _t1 = _time.time()
    print("[STAGE 2/3] Generating pre-computed API JSON files...")
    generate_api_json()
    print(f"[STAGE 2/3] JSON generation done in {_time.time()-_t1:.0f}s")
    _t2 = _time.time()
    print("[STAGE 3/3] Uploading to GCS...")
    upload_to_gcs()
    print(f"[STAGE 3/3] Upload done in {_time.time()-_t2:.0f}s")
    print(f"\n=== COMPLETE === Total: {_time.time()-_t0:.0f}s")
