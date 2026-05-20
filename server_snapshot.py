"""
Sales Dashboard API — reads from local SQLite snapshot (sales.db).
Never writes to the source MySQL DB.

Run: python3 server.py

Auth: Google OAuth restricted to @demo.local accounts.
  Requires env vars: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
  Optional: SECRET_KEY (defaults to random per-restart)
"""
import sqlite3
import os
import functools
import secrets
from collections import defaultdict

from flask import Flask, jsonify, request, send_from_directory, redirect, session, url_for
from authlib.integrations.flask_client import OAuth

app = Flask(__name__, static_folder=os.path.dirname(__file__))
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.environ.get('GAE_ENV', '') == 'standard' or os.environ.get('K_SERVICE', ''),
)
DB = os.path.join(os.path.dirname(__file__), 'sales.db')

ALLOWED_DOMAIN = 'demo.local'

# ── Google OAuth ─────────────────────────────────────────────────────────────

oauth = OAuth(app)
oauth.register(
    name='google',
    client_id=os.environ.get('GOOGLE_CLIENT_ID'),
    client_secret=os.environ.get('GOOGLE_CLIENT_SECRET'),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'},
)


def login_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get('user'):
            if request.path.startswith('/api/'):
                return jsonify({'error': 'unauthorized'}), 401
            return redirect('/login')
        return f(*args, **kwargs)
    return wrapper


@app.route('/login')
def login():
    # Use X-Forwarded headers on GCP to build the correct callback URL
    scheme = request.headers.get('X-Forwarded-Proto', request.scheme)
    redirect_uri = url_for('auth_callback', _external=True, _scheme=scheme)
    nonce = secrets.token_urlsafe(16)
    session['oauth_nonce'] = nonce
    return oauth.google.authorize_redirect(redirect_uri, nonce=nonce)


@app.route('/auth/callback')
def auth_callback():
    token = oauth.google.authorize_access_token()
    nonce = session.pop('oauth_nonce', None)
    user_info = oauth.google.parse_id_token(token, nonce=nonce)

    email = (user_info.get('email') or '').lower()
    if not email.endswith('@' + ALLOWED_DOMAIN):
        from markupsafe import escape
        return '''
            <h2>アクセス拒否</h2>
            <p>このダッシュボードは @demo.local アカウントのみ利用可能です。</p>
            <p>ログインしたアカウント: {}</p>
            <a href="/login">別のアカウントでログイン</a>
        '''.format(escape(email)), 403

    session['user'] = {
        'email': email,
        'name': user_info.get('name', email),
        'picture': user_info.get('picture', ''),
    }
    return redirect('/')


@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')


@app.route('/api/me')
def me():
    user = session.get('user')
    if not user:
        return jsonify({'error': 'unauthorized'}), 401
    return jsonify(user)

# ── Shared constants from config.py ──────────────────────────────────────────
from config import (
    RESIDENTIAL, BASE_TO_OFFICE, BILLING_VIA,
    PROVIDER_SALES_MATCH, SERVICE_CODE_NAMES, PROVIDER_CATEGORY,
    SERVICE_CODE_CATEGORY, SALES_TYPE_CATEGORY, categorize_service,
    smart_facility_badge, _facility_category, _matches_provider, _office_type,
    UPSELL_SERVICES, UPSELL_RESIDENTIAL, UPSELL_RESIDENTIAL_SALES_TYPES, CARE_LEVEL_LIMITS,
    REGION_UNIT_PRICES_BY_CATEGORY, DEFAULT_REGION_RATES, SALES_TYPE_COST_RATIO,
    REGION_UNIT_PRICES, DEFAULT_UNIT_PRICE, OFFICE_REGION,
    get_unit_price, estimate_units_from_bills, CARE_LIMIT_SALES_TYPES,
    infer_care_level, infer_care_level_units, infer_care_level_from_units,
    RESIDENTIAL_PROVIDERS, VISIT_PROVIDERS, EQUIPMENT_PROVIDERS,
    DAY_PROVIDERS, SUPPORT_PROVIDERS, CORP_PROVIDERS,
    _appsheet_mappings,
)
import config as _config_module  # for refresh_appsheet to mutate globals


def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

# ── AppSheet refresh ──────────────────────────────────────────────────────────

@app.route('/api/refresh-appsheet')
@login_required
def refresh_appsheet():
    """Re-fetch AppSheet data and update mappings. No redeploy needed."""
    try:
        import appsheet_sync
        appsheet_sync.API_KEY = os.environ.get('APPSHEET_API_KEY', appsheet_sync.API_KEY)
        if not appsheet_sync.API_KEY:
            return jsonify({'error': 'No APPSHEET_API_KEY configured'}), 500
        data = appsheet_sync.fetch_all()
        appsheet_sync.save_cache(data)
        m = appsheet_sync.build_mappings(data)
        _config_module._appsheet_mappings = m
        _config_module.PROVIDER_CATEGORY.update(m['provider_category'])
        _config_module.RESIDENTIAL = m['residential']
        return jsonify({'status': 'ok', 'offices': len(m['office_names']),
                        'providers': len(m['provider_category'])})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Static ────────────────────────────────────────────────────────────────────

@app.route('/')
@login_required
def index():
    resp = send_from_directory(os.path.dirname(__file__), 'index.html')
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp


@app.route('/favicon.svg')
def favicon():
    return send_from_directory(os.path.dirname(__file__), 'favicon.svg', mimetype='image/svg+xml')


# ── Months ────────────────────────────────────────────────────────────────────

@app.route('/api/months')
@login_required
def months():
    conn = get_db()
    # Order chronologically, newest first; pick fullest month as default
    rows = conn.execute(
        'SELECT service_month, COUNT(*) AS cnt FROM sales_summary GROUP BY service_month ORDER BY service_month DESC'
    ).fetchall()
    # Completeness approximated by distinct base_name count in sales_summary
    # (sales_summary_data removed in single-table migration)
    ssd = conn.execute(
        'SELECT service_month, COUNT(DISTINCT base_name) AS cnt FROM sales_summary '
        'WHERE base_name IS NOT NULL GROUP BY service_month'
    ).fetchall()
    ssd_map = {r['service_month']: r['cnt'] for r in ssd}
    conn.close()
    # Month with >= 40 distinct bases is considered complete
    result = []
    best_month = max(rows, key=lambda r: r['cnt'])['service_month'] if rows else None
    for r in rows:
        m = r['service_month']
        bases = ssd_map.get(m, 0)
        result.append({'month': m, 'complete': bases >= 40, 'bases': bases,
                        'default': m == best_month})
    return jsonify(result)


# ── KPI ───────────────────────────────────────────────────────────────────────

@app.route('/api/kpi')
@login_required
def kpi():
    month = request.args.get('month')
    conn = get_db()

    # Revenue from sales_summary via office_name (billing entity — each bill once)
    ss_totals = conn.execute('''
        SELECT
            COALESCE(SUM(credit_amount), 0) AS total,
            COALESCE(SUM(CASE WHEN debit_account LIKE '6115%%' THEN credit_amount ELSE 0 END), 0) AS insurance,
            COALESCE(SUM(CASE WHEN debit_account LIKE '6120%%' THEN credit_amount ELSE 0 END), 0) AS self_pay
        FROM sales_summary WHERE service_month = ? AND service_office IS NOT NULL
    ''', (month,)).fetchone()
    # non_sale kept as 0 — source table sales_summary_data removed in single-table migration
    totals = {
        'insurance': round(ss_totals['insurance']),
        'self_pay': round(ss_totals['self_pay']),
        'non_sale': 0,
    }

    # Active customers = anyone with sales this month. Inactive (退会) users
    # naturally have no sales, so an explicit flag filter is no longer needed.
    active_cust_ids = set(r[0] for r in conn.execute(
        'SELECT DISTINCT person_id FROM sales_summary WHERE service_month = ?', (month,)))
    customers = {'cnt': len(active_cust_ids)}

    # Facility count = offices with revenue this month (csm-based inactive
    # facilities dropped in single-table migration — if no revenue, no display).
    fac_from_rev = set(r[0] for r in conn.execute(
        "SELECT DISTINCT service_office FROM sales_summary WHERE service_month = ? AND service_office IS NOT NULL AND service_office != ''", (month,)))
    facilities = {'cnt': len(fac_from_rev)}

    total = round(ss_totals['total'])
    n_cust = customers['cnt'] or 1
    n_fac  = facilities['cnt'] or 1

    # Previous month comparison
    prev_row = conn.execute('''
        SELECT service_month FROM sales_summary
        WHERE service_month < ? GROUP BY service_month
        ORDER BY service_month DESC LIMIT 1
    ''', (month,)).fetchone()

    prev = {}
    if prev_row:
        pm = prev_row['service_month']
        pt_ss = conn.execute('''
            SELECT COALESCE(SUM(credit_amount), 0) AS total,
                   COALESCE(SUM(CASE WHEN debit_account LIKE '6115%%' THEN credit_amount ELSE 0 END), 0) AS insurance,
                   COALESCE(SUM(CASE WHEN debit_account LIKE '6120%%' THEN credit_amount ELSE 0 END), 0) AS self_pay
            FROM sales_summary WHERE service_month = ? AND service_office IS NOT NULL
        ''', (pm,)).fetchone()
        # non_sale kept as 0 — source table sales_summary_data removed in single-table migration
        pt = {'insurance': round(pt_ss['insurance']), 'self_pay': round(pt_ss['self_pay']), 'non_sale': 0}
        prev_cust_ids = set(r[0] for r in conn.execute(
            'SELECT DISTINCT person_id FROM sales_summary WHERE service_month = ?', (pm,)))
        pc = {'cnt': len(prev_cust_ids)}
        p_total = round(pt_ss['total'])
        def chg(curr, prev_v):
            return round((curr - prev_v) / prev_v * 100, 1) if prev_v else None
        prev = {
            'prev_month': pm,
            'prev_total': p_total,
            'prev_insurance': pt['insurance'],
            'prev_self_pay': pt['self_pay'],
            'prev_customers': pc['cnt'],
            'prev_non_sale': pt['non_sale'],
            'change_total_pct': chg(total, p_total),
            'change_insurance_pct': chg(totals['insurance'], pt['insurance']),
            'change_self_pay_pct': chg(totals['self_pay'], pt['self_pay']),
            'change_customers_pct': chg(customers['cnt'], pc['cnt']),
            'change_non_sale_pct': chg(totals['non_sale'], pt['non_sale']),
        }

        # New / lost / net — reuse sets already computed above
        n_new = len(active_cust_ids - prev_cust_ids)
        n_lost = len(prev_cust_ids - active_cust_ids)
        prev['new_customers'] = n_new
        prev['lost_customers'] = n_lost
        prev['net_customers'] = n_new - n_lost

    # ── Build status_sentence ────────────────────────────────────────────────
    def _fmt_yen_short(v):
        """Format yen as short string: e.g. 327000000 -> '¥327M', 12200000 -> '¥12.2M'"""
        m = v / 1_000_000
        if m >= 100:
            return f'¥{m:.0f}M'
        elif m >= 10:
            return f'¥{m:.1f}M' if m != int(m) else f'¥{int(m)}M'
        else:
            return f'¥{m:.1f}M'

    # Build lightweight status sentence (no heavy analysis queries — those are in /api/analysis)
    if prev:
        change_pct = prev.get('change_total_pct')
        change_str = ''
        if change_pct is not None:
            sign = '+' if change_pct >= 0 else ''
            change_str = f'（前月比 {sign}{change_pct}%）'
        n_new = prev.get('new_customers', 0)
        n_lost = prev.get('lost_customers', 0)
        parts = [f'売上 {_fmt_yen_short(total)}{change_str}']
        parts.append(f'利用者 {customers["cnt"]:,}名')
        if n_new or n_lost:
            parts.append(f'新規{n_new}名・終了{n_lost}名')
        parts.append(f'{facilities["cnt"]}拠点')
        status_sentence = ' ｜ '.join(parts)
    else:
        status_sentence = f'売上 {_fmt_yen_short(total)} ｜ 利用者 {customers["cnt"]:,}名 ｜ {facilities["cnt"]}拠点'

    conn.close()
    return jsonify({
        'insurance': totals['insurance'],
        'self_pay':  totals['self_pay'],
        'non_sale':  totals['non_sale'],
        'total':     total,
        'customers': customers['cnt'],
        'facilities': facilities['cnt'],
        'avg_per_customer':  round(total / n_cust),
        'avg_per_facility':  round(total / n_fac),
        'avg_ins_per_customer': round(totals['insurance'] / n_cust),
        'avg_pay_per_customer': round(totals['self_pay'] / n_cust),
        'status_sentence': status_sentence,
        **prev,
    })


# ── Facilities ────────────────────────────────────────────────────────────────

@app.route('/api/facilities')
@login_required
def facilities():
    """
    Two views:
      billing  — what each facility billed directly (from sales_summary)
      resident — total bills for all people who live there (all their services combined)
    """
    month = request.args.get('month')
    view  = request.args.get('view', 'billing')
    compare_month = request.args.get('compare_month')
    conn  = get_db()

    if view == 'billing':
        # Where a service_office hosts multiple residential categories this
        # month (e.g. 東千石 runs サ高住 AND 障がい者GH), split its billing row
        # to mirror the 居住者合計 view — "GH東千石", "サ高住東千石" etc. The
        # NUMBERS stay strictly billing-based: each bill contributes its own
        # credit_amount to whichever split owns it (no resident attribution).
        #   residential bill  → its own display_category decides the split
        #   non-residential   → its base_name's primary residential category
        #                       (base_name like 0026_障がい者GH 東千石 → GH,
        #                        0009_東千石拠点 where サ高住 dominates → サ高住)
        #
        # [1] residential revenue per (ofc, disp_cat) — detect split-worthy offices
        res_by_ofc = defaultdict(dict)
        for r in conn.execute('''
            SELECT service_office AS ofc,
                   CASE WHEN sales_type LIKE '%サ高住%' THEN 'サ高住' ELSE category END AS dc,
                   COALESCE(SUM(credit_amount), 0) AS amt
            FROM sales_summary
            WHERE service_month = ? AND is_residential = 1 AND service_office IS NOT NULL
            GROUP BY ofc, dc
        ''', (month,)).fetchall():
            res_by_ofc[r['ofc']][r['dc']] = r['amt']
        splittable = {ofc for ofc, cats in res_by_ofc.items() if len(cats) > 1}

        # [2] primary residential category per (ofc, base_name)
        bn_primary = {}
        for r in conn.execute('''
            SELECT service_office AS ofc, base_name,
                   CASE WHEN sales_type LIKE '%サ高住%' THEN 'サ高住' ELSE category END AS dc,
                   COALESCE(SUM(credit_amount), 0) AS amt
            FROM sales_summary
            WHERE service_month = ? AND is_residential = 1 AND service_office IS NOT NULL
            GROUP BY ofc, base_name, dc
        ''', (month,)).fetchall():
            key = (r['ofc'], r['base_name'])
            prev = bn_primary.get(key)
            if prev is None or r['amt'] > prev[1]:
                bn_primary[key] = (r['dc'], r['amt'])

        def _display_name(ofc, base_name, sales_type, is_residential, category):
            if not ofc or ofc not in splittable:
                return ofc
            if is_residential:
                dc = 'サ高住' if sales_type and 'サ高住' in sales_type else category
            else:
                bp = bn_primary.get((ofc, base_name))
                dc = bp[0] if bp else None
            if not dc:
                # no residential at this base_name — fall back to a dominant split
                # across the whole service_office so the bill still lands somewhere
                cats = res_by_ofc.get(ofc, {})
                dc = max(cats, key=cats.get) if cats else None
            if not dc:
                return ofc
            return CATEGORY_TO_PREFIX.get(dc, dc) + ofc

        # [3] all bills this month — iterate and bucket into display-name groups
        bill_rows = conn.execute('''
            SELECT person_id, service_office AS ofc, base_name, sales_type,
                   category, is_residential,
                   COALESCE(SUM(credit_amount), 0) AS amount,
                   COALESCE(SUM(CASE WHEN debit_account LIKE '6115%' THEN credit_amount ELSE 0 END), 0) AS insurance,
                   COALESCE(SUM(CASE WHEN debit_account LIKE '6120%' THEN credit_amount ELSE 0 END), 0) AS self_pay
            FROM sales_summary
            WHERE service_month = ? AND service_office IS NOT NULL
            GROUP BY person_id, ofc, base_name, sales_type, category, is_residential
        ''', (month,)).fetchall()

        agg = defaultdict(lambda: {
            'total': 0, 'insurance': 0, 'self_pay': 0,
            'cat_rev': {}, 'providers': set(), 'person_ids': set(),
        })
        for r in bill_rows:
            name = _display_name(r['ofc'], r['base_name'], r['sales_type'],
                                 r['is_residential'], r['category'])
            if not name:
                continue
            a = agg[name]
            a['total'] += r['amount']
            a['insurance'] += r['insurance']
            a['self_pay'] += r['self_pay']
            cat = categorize_service(r['sales_type'])
            a['cat_rev'][cat] = a['cat_rev'].get(cat, 0) + r['amount']
            a['providers'].add(r['sales_type'])
            if r['person_id']:
                a['person_ids'].add(r['person_id'])

        result = []
        for name, a in agg.items():
            if a['total'] <= 0 and not a['person_ids']:
                continue
            cat = _facility_category(','.join(a['providers']))
            cats_ordered = []
            # Prefer PROVIDER_CATEGORY order for display
            for p in a['providers']:
                pc = PROVIDER_CATEGORY.get(p)
                if pc and pc not in cats_ordered:
                    cats_ordered.append(pc)
            for rc in a['cat_rev']:
                if rc not in cats_ordered:
                    cats_ordered.append(rc)
            if not cats_ordered:
                cats_ordered = [cat]
            result.append({
                'name': name,
                'category': cat,
                'categories': cats_ordered,
                'insurance': round(a['insurance']),
                'self_pay': round(a['self_pay']),
                'total': round(a['total']),
                'active': len(a['person_ids']),
                'rev_by_cat': {k: round(v) for k, v in a['cat_rev'].items()},
            })
        result.sort(key=lambda x: -x['total'])

        if compare_month:
            # Recompute previous-month display names using THAT month's split
            # detection, so the same logical facility maps across months.
            prev_res_by_ofc = defaultdict(dict)
            for r in conn.execute('''
                SELECT service_office AS ofc,
                       CASE WHEN sales_type LIKE '%サ高住%' THEN 'サ高住' ELSE category END AS dc,
                       COALESCE(SUM(credit_amount), 0) AS amt
                FROM sales_summary
                WHERE service_month = ? AND is_residential = 1 AND service_office IS NOT NULL
                GROUP BY ofc, dc
            ''', (compare_month,)).fetchall():
                prev_res_by_ofc[r['ofc']][r['dc']] = r['amt']
            prev_splittable = {ofc for ofc, cats in prev_res_by_ofc.items() if len(cats) > 1}
            prev_bn_primary = {}
            for r in conn.execute('''
                SELECT service_office AS ofc, base_name,
                       CASE WHEN sales_type LIKE '%サ高住%' THEN 'サ高住' ELSE category END AS dc,
                       COALESCE(SUM(credit_amount), 0) AS amt
                FROM sales_summary
                WHERE service_month = ? AND is_residential = 1 AND service_office IS NOT NULL
                GROUP BY ofc, base_name, dc
            ''', (compare_month,)).fetchall():
                key = (r['ofc'], r['base_name'])
                prev = prev_bn_primary.get(key)
                if prev is None or r['amt'] > prev[1]:
                    prev_bn_primary[key] = (r['dc'], r['amt'])
            prev_agg = defaultdict(float)
            for r in conn.execute('''
                SELECT service_office AS ofc, base_name, sales_type,
                       category, is_residential,
                       COALESCE(SUM(credit_amount), 0) AS amt
                FROM sales_summary
                WHERE service_month = ? AND service_office IS NOT NULL
                GROUP BY ofc, base_name, sales_type, category, is_residential
            ''', (compare_month,)).fetchall():
                ofc = r['ofc']
                if ofc not in prev_splittable:
                    name = ofc
                else:
                    if r['is_residential']:
                        dc = 'サ高住' if r['sales_type'] and 'サ高住' in r['sales_type'] else r['category']
                    else:
                        bp = prev_bn_primary.get((ofc, r['base_name']))
                        dc = bp[0] if bp else None
                    if not dc:
                        cats = prev_res_by_ofc.get(ofc, {})
                        dc = max(cats, key=cats.get) if cats else None
                    name = (CATEGORY_TO_PREFIX.get(dc, dc) + ofc) if dc else ofc
                prev_agg[name] += r['amt']
            for item in result:
                pt = round(prev_agg.get(item['name'], 0))
                item['prev_total'] = pt
                item['change_pct'] = round((item['total'] - pt) / pt * 100, 1) if pt else None

        # Sparkline trend — group each month's bills by display-name using that
        # month's split detection. Expensive-looking but O(months × bills).
        trend_by_name = defaultdict(lambda: defaultdict(float))
        trend_months = [r['service_month'] for r in conn.execute('''
            SELECT DISTINCT service_month FROM sales_summary
            WHERE service_month <= ? AND service_office IS NOT NULL
            ORDER BY service_month DESC LIMIT 6
        ''', (month,)).fetchall()]
        for tm in sorted(trend_months):
            # Rebuild split detection for month tm
            t_res = defaultdict(dict)
            for r in conn.execute('''
                SELECT service_office AS ofc,
                       CASE WHEN sales_type LIKE '%サ高住%' THEN 'サ高住' ELSE category END AS dc,
                       COALESCE(SUM(credit_amount), 0) AS amt
                FROM sales_summary
                WHERE service_month = ? AND is_residential = 1 AND service_office IS NOT NULL
                GROUP BY ofc, dc
            ''', (tm,)).fetchall():
                t_res[r['ofc']][r['dc']] = r['amt']
            t_split = {ofc for ofc, cats in t_res.items() if len(cats) > 1}
            t_bn = {}
            for r in conn.execute('''
                SELECT service_office AS ofc, base_name,
                       CASE WHEN sales_type LIKE '%サ高住%' THEN 'サ高住' ELSE category END AS dc,
                       COALESCE(SUM(credit_amount), 0) AS amt
                FROM sales_summary
                WHERE service_month = ? AND is_residential = 1 AND service_office IS NOT NULL
                GROUP BY ofc, base_name, dc
            ''', (tm,)).fetchall():
                key = (r['ofc'], r['base_name'])
                prev = t_bn.get(key)
                if prev is None or r['amt'] > prev[1]:
                    t_bn[key] = (r['dc'], r['amt'])
            for r in conn.execute('''
                SELECT service_office AS ofc, base_name, sales_type,
                       category, is_residential,
                       COALESCE(SUM(credit_amount), 0) AS amt
                FROM sales_summary
                WHERE service_month = ? AND service_office IS NOT NULL
                GROUP BY ofc, base_name, sales_type, category, is_residential
            ''', (tm,)).fetchall():
                ofc = r['ofc']
                if ofc not in t_split:
                    name = ofc
                else:
                    if r['is_residential']:
                        dc = 'サ高住' if r['sales_type'] and 'サ高住' in r['sales_type'] else r['category']
                    else:
                        bp = t_bn.get((ofc, r['base_name']))
                        dc = bp[0] if bp else None
                    if not dc:
                        cats = t_res.get(ofc, {})
                        dc = max(cats, key=cats.get) if cats else None
                    name = (CATEGORY_TO_PREFIX.get(dc, dc) + ofc) if dc else ofc
                trend_by_name[name][tm] += r['amt']
        for item in result:
            months = trend_by_name.get(item['name'], {})
            item['trend'] = [{'month': m, 'total': round(v)} for m, v in sorted(months.items())][-6:]

    else:
        # Residential facility view: one row per (service_office, display_category).
        # display_category splits '有料老人ホーム' into '有料老人ホーム' / 'サ高住'.
        # total and rev_by_cat reflect 居住者の全サービス請求 — ALL bills (not just
        # residential) for residents of that (facility, category) group.

        # Step 1: identify residents in each (facility, display_category) group
        res_rows = conn.execute('''
            SELECT person_id, service_office,
                   CASE WHEN sales_type LIKE '%サ高住%' THEN 'サ高住' ELSE category END AS display_category,
                   sales_type, SUM(credit_amount) AS amt
            FROM sales_summary
            WHERE service_month = ?
              AND is_residential = 1
              AND service_office IS NOT NULL
              AND category IS NOT NULL
            GROUP BY person_id, service_office, display_category, sales_type
        ''', (month,)).fetchall()

        # Build groups: (facility, category) → {person_ids, sales_type_revenue}
        groups = defaultdict(lambda: {'aids': set(), 'provider_revenue': defaultdict(float)})
        for r in res_rows:
            key = (r['service_office'], r['display_category'])
            groups[key]['aids'].add(r['person_id'])
            groups[key]['provider_revenue'][r['sales_type']] += r['amt']

        # Step 2: for each group, aggregate ALL bills (any sales_type) for its residents
        result = []
        for (facility, cat), g in groups.items():
            aids = list(g['aids'])
            aid_ph = ','.join('?' * len(aids))
            # Exclude bills from OTHER residential categories. A resident may
            # carry a stray bill from a previous residence (e.g., サ高住
            # resident with a lingering 共同生活援助 bill from their old GH) —
            # that belongs to that other category's split, not this one.
            bill_rows = conn.execute(f'''
                SELECT category AS bill_cat,
                       CASE WHEN sales_type LIKE '%サ高住%' THEN 'サ高住' ELSE category END AS disp_cat,
                       COALESCE(SUM(credit_amount), 0) AS amt
                FROM sales_summary
                WHERE service_month = ? AND person_id IN ({aid_ph})
                  AND NOT (is_residential = 1 AND
                           (CASE WHEN sales_type LIKE '%サ高住%' THEN 'サ高住' ELSE category END) != ?)
                GROUP BY disp_cat
            ''', (month, *aids, cat)).fetchall()
            rev_by_cat = {r['disp_cat']: round(r['amt']) for r in bill_rows if r['amt']}
            total = sum(rev_by_cat.values())
            # Top residential sales_type within this group (for badge display)
            provider_name = max(g['provider_revenue'], key=g['provider_revenue'].get) if g['provider_revenue'] else cat
            result.append({
                'name': facility,
                'category': cat,
                'provider_name': provider_name,
                'total': total,
                'residents': len(aids),
                'rev_by_cat': rev_by_cat,
            })
        # Where a physical facility hosts multiple residential categories
        # (e.g. 東千石 runs 有料老人ホーム AND 障がい者GH), split into distinct
        # logical facilities by prefixing each category. Each compound name is
        # drillable independently (detail/trend parse the prefix back).
        per_physical = defaultdict(int)
        for r in result:
            per_physical[r['name']] += 1
        for r in result:
            if per_physical[r['name']] > 1:
                r['physical_name'] = r['name']
                r['name'] = CATEGORY_TO_PREFIX.get(r['category'], r['category']) + r['name']
        result.sort(key=lambda x: -x['total'])

    conn.close()
    return jsonify(result)


# Category display_category → prefix used in compound names for split residential
# facilities. Ordered longer-first so prefix parsing matches correctly.
CATEGORY_TO_PREFIX = {
    '認知症GH':     '認知症GH',
    '介護付有料':   '介護付',
    'サ高住':       'サ高住',
    '特養':         '特養',
    '有料老人ホーム':  '有料',
    '障がい者GH':   'GH',
}
# Reverse lookup ordered for prefix parsing (longer prefixes first).
_PREFIX_CAT_ORDERED = sorted(
    [(p, c) for c, p in CATEGORY_TO_PREFIX.items()],
    key=lambda kv: -len(kv[0]),
)


def parse_compound_facility_name(name):
    """Split a resident-view compound name (e.g. '有料東千石') into
    (category_filter, physical_name). If no known prefix, return (None, name).
    The resident-view facilities endpoint produces these compound names only
    for physical facilities that host multiple categories, so the detail and
    trend endpoints call this to filter residents accordingly."""
    if not name:
        return None, name
    for prefix, cat in _PREFIX_CAT_ORDERED:
        if name.startswith(prefix) and len(name) > len(prefix):
            return cat, name[len(prefix):]
    return None, name


def _resolve_office_names(name):
    """
    Resolve a facility name (base_name or office_name) to the list of
    office_names used in customer_service_map.
    Uses BASE_TO_OFFICE for known mismatches, falls back to substring matching.
    """
    # Check explicit mapping (base_name → office_names)
    if name in BASE_TO_OFFICE:
        return BASE_TO_OFFICE[name]
    # Check reverse: if name IS an office_name that maps from a base_name
    for base, offices in BASE_TO_OFFICE.items():
        if name in offices:
            return offices
    return None  # use default substring matching


@app.route('/api/facility-detail')
@login_required
def facility_detail():
    """Drill-down for one facility by office_name. All lookups use exact keys.
    Accepts compound names like '有料東千石' (split residential facilities);
    parses the prefix back to (category, physical_name) and filters residents
    to that category only while billing/users still use the physical name."""
    month = request.args.get('month')
    raw_name = request.args.get('name') or ''
    view = request.args.get('view', 'billing')  # 'billing' or 'resident'
    resident_cat, name = parse_compound_facility_name(raw_name)
    conn  = get_db()

    # Resident-centric path triggers ONLY when caller explicitly asks
    # view=resident (from 居住者合計). A compound name with view=billing uses
    # billing-split attribution instead — same per-bill rules as the main
    # 拠点別請求 list (by sales_type + base_name primary), never resident
    # aggregation. Otherwise (non-compound + billing): facility-centric.
    use_resident_view = view == 'resident'
    use_billing_split = view == 'billing' and bool(resident_cat)
    resident_aids = []
    if use_resident_view:
        aid_cat_clause = (
            " AND (CASE WHEN sales_type LIKE '%サ高住%' THEN 'サ高住' ELSE category END) = ?"
            if resident_cat else ""
        )
        aid_cat_params = (resident_cat,) if resident_cat else ()
        resident_aids = [r['person_id'] for r in conn.execute(f'''
            SELECT DISTINCT person_id FROM sales_summary
            WHERE service_month = ? AND service_office = ? AND is_residential = 1
              AND person_id IS NOT NULL AND person_id != 'FACILITY'{aid_cat_clause}
        ''', (month, name, *aid_cat_params)).fetchall()]
    cat_clause = " AND (CASE WHEN sales_type LIKE '%サ高住%' THEN 'サ高住' ELSE category END) = ?" if resident_cat else ""
    cat_params = (resident_cat,) if resident_cat else ()

    # For compound + billing view: mirror facilities() billing-split attribution
    # so each bill at the physical facility is filtered to only those owned by
    # this split (by sales_type for residential, by base_name primary for non-
    # residential). Numbers stay purely bill-based.
    def _split_match(base_name, sales_type, is_residential, category):
        return True  # default: all bills pass
    if use_billing_split:
        # base_name → (primary residential category, revenue)
        _bn_primary = {}
        for r in conn.execute('''
            SELECT base_name,
                   CASE WHEN sales_type LIKE '%サ高住%' THEN 'サ高住' ELSE category END AS dc,
                   COALESCE(SUM(credit_amount), 0) AS amt
            FROM sales_summary
            WHERE service_month = ? AND service_office = ? AND is_residential = 1
            GROUP BY base_name, dc
        ''', (month, name)).fetchall():
            prev = _bn_primary.get(r['base_name'])
            if prev is None or r['amt'] > prev[1]:
                _bn_primary[r['base_name']] = (r['dc'], r['amt'])
        # service_office → dominant residential category (fallback for orphans)
        _ofc_cats = {}
        for r in conn.execute('''
            SELECT CASE WHEN sales_type LIKE '%サ高住%' THEN 'サ高住' ELSE category END AS dc,
                   COALESCE(SUM(credit_amount), 0) AS amt
            FROM sales_summary
            WHERE service_month = ? AND service_office = ? AND is_residential = 1
            GROUP BY dc
        ''', (month, name)).fetchall():
            _ofc_cats[r['dc']] = r['amt']
        _dominant = max(_ofc_cats, key=_ofc_cats.get) if _ofc_cats else None
        def _split_match(base_name, sales_type, is_residential, category):
            if is_residential:
                dc = 'サ高住' if sales_type and 'サ高住' in sales_type else category
            else:
                bp = _bn_primary.get(base_name)
                dc = bp[0] if bp else _dominant
            return dc == resident_cat

    # Billing breakdown
    # • Non-compound: sales_types billed at this service_office
    # • Compound:    ALL bills of the split's residents (any service_office),
    #                MINUS residential bills from other categories. A resident
    #                occasionally carries a bill from a previous residence
    #                (e.g., サ高住 resident with lingering 共同生活援助 from
    #                their old GH) — that belongs to the OTHER split, not here.
    def _other_res_clause(prefix=''):
        if not resident_cat:
            return ''
        p = prefix + '.' if prefix else ''
        return (
            f" AND NOT ({p}is_residential = 1 AND "
            f"(CASE WHEN {p}sales_type LIKE '%サ高住%' THEN 'サ高住' ELSE {p}category END) != ?)"
        )
    other_res_clause = _other_res_clause('')
    ss_other_res_clause = _other_res_clause('ss')
    other_res_params = (resident_cat,) if resident_cat else ()
    if use_resident_view and resident_aids:
        aid_ph = ','.join('?' * len(resident_aids))
        billing_raw = conn.execute(f'''
            SELECT sales_type,
                COALESCE(SUM(credit_amount), 0) AS total,
                COALESCE(SUM(CASE WHEN debit_account LIKE '6115%' THEN credit_amount ELSE 0 END), 0) AS insurance,
                COALESCE(SUM(CASE WHEN debit_account LIKE '6120%' THEN credit_amount ELSE 0 END), 0) AS self_pay
            FROM sales_summary
            WHERE service_month = ? AND person_id IN ({aid_ph}){other_res_clause}
            GROUP BY sales_type ORDER BY total DESC
        ''', (month, *resident_aids, *other_res_params)).fetchall()
    elif use_billing_split:
        raw = conn.execute('''
            SELECT sales_type, base_name, category, is_residential,
                COALESCE(SUM(credit_amount), 0) AS total,
                COALESCE(SUM(CASE WHEN debit_account LIKE '6115%' THEN credit_amount ELSE 0 END), 0) AS insurance,
                COALESCE(SUM(CASE WHEN debit_account LIKE '6120%' THEN credit_amount ELSE 0 END), 0) AS self_pay
            FROM sales_summary
            WHERE service_month = ? AND service_office = ?
            GROUP BY sales_type, base_name, category, is_residential
        ''', (month, name)).fetchall()
        _agg = {}
        for r in raw:
            if not _split_match(r['base_name'], r['sales_type'], r['is_residential'], r['category']):
                continue
            st = r['sales_type']
            a = _agg.setdefault(st, {'total': 0, 'insurance': 0, 'self_pay': 0})
            a['total'] += r['total']; a['insurance'] += r['insurance']; a['self_pay'] += r['self_pay']
        billing_raw = [dict(sales_type=st, **v) for st, v in sorted(_agg.items(), key=lambda kv: -kv[1]['total'])]
    else:
        # Phase D: detail breakdown by sub_sales_type (falls back to sales_type
        # when sub is NULL). Gives team the granular "洗濯 / 食事 / 就労継続
        # 支援B型" view instead of a single "就労事業収入" row.
        billing_raw = conn.execute('''
            SELECT COALESCE(NULLIF(sub_sales_type, ''), sales_type) AS detail_name,
                MIN(sales_type) AS sales_type,
                COALESCE(SUM(credit_amount), 0) AS total,
                COALESCE(SUM(CASE WHEN debit_account LIKE '6115%' THEN credit_amount ELSE 0 END), 0) AS insurance,
                COALESCE(SUM(CASE WHEN debit_account LIKE '6120%' THEN credit_amount ELSE 0 END), 0) AS self_pay
            FROM sales_summary
            WHERE service_month = ? AND service_office = ?
            GROUP BY detail_name ORDER BY total DESC
        ''', (month, name)).fetchall()

    import re as _re
    def _pretty(s):
        # Strip the leading NNNN_ prefix from sub_sales_type values
        # ('2250_就労事業収入（洗濯）' → '就労事業収入（洗濯）').
        return _re.sub(r'^\d{3,5}_', '', s) if s else s

    billing = [{
        'service_name': _pretty(r['detail_name'] if 'detail_name' in r.keys() else r['sales_type']),
        'category': categorize_service(r['sales_type']),
        'insurance': round(r['insurance']),
        'self_pay': round(r['self_pay']),
        'total': round(r['total']),
    } for r in billing_raw]

    # Residents section: populate whenever the facility has residents, so the
    # modal's 居住者の全サービス請求 cards stay visible after 居住者合計 deletion
    # (Phase A). This doesn't affect billing totals — the billing breakdown
    # above is unchanged.
    has_residential = conn.execute(f'''
        SELECT 1 FROM sales_summary
        WHERE service_month = ? AND service_office = ? AND is_residential = 1{cat_clause}
        LIMIT 1
    ''', (month, name, *cat_params)).fetchone() is not None

    # Residents: person_ids with a residential sales_type at this office this month.
    # Their res_provider is the residential sales_type they were billed for.
    # Services shown: ALL sales for that user at this office (not just residential).
    residents = []
    if has_residential:
        residents = conn.execute(f'''
            WITH residents_here AS (
                SELECT DISTINCT person_id,
                       (SELECT sales_type FROM sales_summary s2
                        WHERE s2.service_month = sales_summary.service_month
                          AND s2.service_office = sales_summary.service_office
                          AND s2.person_id = sales_summary.person_id
                          AND s2.is_residential = 1
                        GROUP BY sales_type ORDER BY SUM(credit_amount) DESC LIMIT 1) AS res_provider
                FROM sales_summary
                WHERE service_month = ? AND service_office = ? AND is_residential = 1{cat_clause}
            )
            SELECT r.person_id, nl.name AS full_name, r.res_provider,
                ss.sales_type, ss.base_name AS billing_facility,
                COALESCE(SUM(ss.credit_amount), 0) AS amount,
                COALESCE(SUM(CASE WHEN ss.debit_account LIKE '6115%' THEN ss.credit_amount ELSE 0 END), 0) AS insurance,
                COALESCE(SUM(CASE WHEN ss.debit_account LIKE '6120%' THEN ss.credit_amount ELSE 0 END), 0) AS self_pay
            FROM residents_here r
            LEFT JOIN _name_lookup nl ON nl.person_id = r.person_id
            JOIN sales_summary ss ON ss.person_id = r.person_id AND ss.service_month = ?{ss_other_res_clause}
            GROUP BY r.person_id, nl.name, r.res_provider, ss.sales_type, ss.base_name
            ORDER BY r.person_id, amount DESC
        ''', (month, name, *cat_params, month, *other_res_params)).fetchall()

    # Service users
    # • Non-compound: users billed at this service_office (facility-centric)
    # • Compound:    the split's residents, aggregated across ALL their bills
    #                (any service_office) — mirrors the billing breakdown above
    if use_resident_view and resident_aids:
        aid_ph = ','.join('?' * len(resident_aids))
        user_raw = conn.execute(f'''
            SELECT ss.person_id, nl.name AS full_name, ss.sales_type,
                COALESCE(SUM(ss.credit_amount), 0) AS amount,
                COALESCE(SUM(CASE WHEN ss.debit_account LIKE '6115%' THEN ss.credit_amount ELSE 0 END), 0) AS insurance,
                COALESCE(SUM(CASE WHEN ss.debit_account LIKE '6120%' THEN ss.credit_amount ELSE 0 END), 0) AS self_pay
            FROM sales_summary ss
            LEFT JOIN _name_lookup nl ON nl.person_id = ss.person_id
            WHERE ss.service_month = ? AND ss.person_id IN ({aid_ph}){ss_other_res_clause}
            GROUP BY ss.person_id, nl.name, ss.sales_type
            ORDER BY amount DESC
        ''', (month, *resident_aids, *other_res_params)).fetchall()
    elif use_billing_split:
        # Users = person_ids whose bills at this physical facility fall under
        # this split (per _split_match). Filter in Python after fetching all
        # (person_id × base_name × sales_type) rows.
        raw = conn.execute('''
            SELECT ss.person_id, nl.name AS full_name, ss.sales_type,
                   ss.base_name, ss.category, ss.is_residential,
                COALESCE(SUM(ss.credit_amount), 0) AS amount,
                COALESCE(SUM(CASE WHEN ss.debit_account LIKE '6115%' THEN ss.credit_amount ELSE 0 END), 0) AS insurance,
                COALESCE(SUM(CASE WHEN ss.debit_account LIKE '6120%' THEN ss.credit_amount ELSE 0 END), 0) AS self_pay
            FROM sales_summary ss
            LEFT JOIN _name_lookup nl ON nl.person_id = ss.person_id
            WHERE ss.service_month = ? AND ss.service_office = ?
            GROUP BY ss.person_id, nl.name, ss.sales_type, ss.base_name, ss.category, ss.is_residential
        ''', (month, name)).fetchall()
        user_raw = [r for r in raw
                    if _split_match(r['base_name'], r['sales_type'], r['is_residential'], r['category'])]
    else:
        user_raw = conn.execute('''
            SELECT ss.person_id, nl.name AS full_name, ss.sales_type,
                COALESCE(SUM(ss.credit_amount), 0) AS amount,
                COALESCE(SUM(CASE WHEN ss.debit_account LIKE '6115%' THEN ss.credit_amount ELSE 0 END), 0) AS insurance,
                COALESCE(SUM(CASE WHEN ss.debit_account LIKE '6120%' THEN ss.credit_amount ELSE 0 END), 0) AS self_pay
            FROM sales_summary ss
            LEFT JOIN _name_lookup nl ON nl.person_id = ss.person_id
            WHERE ss.service_month = ? AND ss.service_office = ?
            GROUP BY ss.person_id, nl.name, ss.sales_type
            ORDER BY amount DESC
        ''', (month, name)).fetchall()

    # Aggregate per user, derive provider_name from sales_type category
    user_agg = {}
    for r in user_raw:
        cat = categorize_service(r['sales_type'])
        key = r['person_id']
        if key not in user_agg:
            user_agg[key] = {'person_id': r['person_id'], 'full_name': r['full_name'],
                             'provider_name': cat, 'amount': 0, 'insurance': 0, 'self_pay': 0}
        user_agg[key]['amount'] += r['amount']
        user_agg[key]['insurance'] += r['insurance']
        user_agg[key]['self_pay'] += r['self_pay']
    users = sorted(user_agg.values(), key=lambda x: -x['amount'])

    # Billing entities: where this facility's revenue is actually billed through
    billing_entities = conn.execute('''
        SELECT office_name AS entity, COALESCE(SUM(credit_amount), 0) AS amount
        FROM sales_summary
        WHERE service_month = ? AND service_office = ? AND office_name != service_office
        GROUP BY office_name ORDER BY amount DESC
    ''', (month, name)).fetchall()

    conn.close()
    return jsonify({
        'billing':          [dict(r) for r in billing],
        'residents':        [dict(r) for r in residents],
        'users':            [dict(r) for r in users],
        'billing_entities': [dict(r) for r in billing_entities],
    })


# ── Services ──────────────────────────────────────────────────────────────────

@app.route('/api/services')
@login_required
def services():
    month = request.args.get('month')
    compare_month = request.args.get('compare_month')
    conn  = get_db()
    rows  = conn.execute('''
        SELECT
            sales_type,
            COALESCE(SUM(credit_amount), 0) AS total,
            COUNT(DISTINCT person_id)        AS customers
        FROM sales_summary
        WHERE service_month = ?
        GROUP BY sales_type
        ORDER BY total DESC
    ''', (month,)).fetchall()
    result = [dict(r) | {'category': categorize_service(r['sales_type'])} for r in rows]

    if compare_month:
        prev_rows = conn.execute('''
            SELECT sales_type, COALESCE(SUM(credit_amount),0) AS total,
                   COUNT(DISTINCT person_id) AS customers
            FROM sales_summary WHERE service_month = ?
            GROUP BY sales_type
        ''', (compare_month,)).fetchall()
        prev_map = {r['sales_type']: dict(r) for r in prev_rows}
        for item in result:
            prev = prev_map.get(item['sales_type'])
            pt = prev['total'] if prev else 0
            item['prev_total'] = pt
            item['change_pct'] = round((item['total'] - pt) / pt * 100, 1) if pt else None

    conn.close()
    return jsonify(result)


# ── Persons ───────────────────────────────────────────────────────────────────

@app.route('/api/persons')
@login_required
def persons():
    month = request.args.get('month')
    q     = request.args.get('q', '').strip()
    max_rows = request.args.get('limit', '200')
    limit_clause = '' if max_rows == '0' else f'LIMIT {int(max_rows)}'
    conn  = get_db()

    base_sql = '''
        SELECT
            ss.person_id,
            nl.name AS full_name,
            COALESCE(SUM(ss.credit_amount), 0) AS total,
            COALESCE(SUM(CASE WHEN ss.debit_account LIKE '6115%%' THEN ss.credit_amount ELSE 0 END), 0) AS insurance,
            COALESCE(SUM(CASE WHEN ss.debit_account LIKE '6120%%' THEN ss.credit_amount ELSE 0 END), 0) AS self_pay,
            COUNT(DISTINCT ss.sales_type)  AS service_count,
            COUNT(DISTINCT ss.base_name)   AS facility_count
        FROM sales_summary ss
        LEFT JOIN _name_lookup nl ON nl.person_id = ss.person_id
    '''
    # q search: by name, person_id, or service_office where they have sales
    # (csm-based office search removed in single-table migration).
    if q:
        rows = conn.execute(base_sql + f'''
            LEFT JOIN sales_summary ss2
              ON ss2.person_id = ss.person_id AND ss2.service_month = ss.service_month
                 AND ss2.service_office LIKE ?
            WHERE ss.service_month = ?
              AND (nl.name LIKE ? OR ss.person_id = ? OR ss2.person_id IS NOT NULL)
            GROUP BY ss.person_id, nl.name
            ORDER BY total DESC
            {limit_clause}
        ''', (f'%{q}%', month, f'%{q}%', q)).fetchall()
    else:
        rows = conn.execute(base_sql + f'''
            WHERE ss.service_month = ?
            GROUP BY ss.person_id, nl.name
            ORDER BY total DESC
            {limit_clause}
        ''', (month,)).fetchall()

    # Residence info: service_office of the user's residential sales this month
    # (csm-based residency replaced with sales_summary.is_residential).
    aid_list = [r['person_id'] for r in rows]
    residence_map = {}
    if aid_list:
        aid_ph = ','.join('?' * len(aid_list))
        res_rows = conn.execute(f'''
            SELECT person_id, service_office FROM sales_summary
            WHERE service_month = ? AND is_residential = 1
              AND service_office IS NOT NULL
              AND person_id IN ({aid_ph})
        ''', (month, *aid_list)).fetchall()
        for r2 in res_rows:
            if r2['person_id'] not in residence_map:
                residence_map[r2['person_id']] = r2['service_office']

    result = [dict(r) | {'residence': residence_map.get(r['person_id'])} for r in rows]

    conn.close()
    return jsonify(result)


@app.route('/api/person-detail')
@login_required
def person_detail():
    """All bills for one person across all services and facilities."""
    person_id = request.args.get('id')
    month     = request.args.get('month')
    conn      = get_db()

    # Single-table migration: name from _name_lookup (sales_summary.name),
    # demographic fields (customer_type, gender, birthday, flag) no longer
    # available. Keys preserved for frontend compat with null values.
    name_row = conn.execute(
        'SELECT name FROM _name_lookup WHERE person_id = ?', (person_id,)
    ).fetchone()
    info = {
        'person_id': person_id,
        'full_name': name_row['name'] if name_row else None,
        'customer_type': None,
        'gender': None,
        'birthday': None,
        'flag': None,
    }

    # services_used replaced with sales-based aggregation (csm removed in
    # single-table migration). Per sales_type + service_office the user was
    # billed for, across all months. Contract/room/status fields no longer
    # available — keys preserved for frontend compat.
    services_used = conn.execute('''
        SELECT
            sales_type AS provider_name,
            service_office AS office_name,
            NULL AS room,
            '利用中' AS status,
            MIN(service_month) AS contract_start,
            NULL AS contract_end
        FROM sales_summary
        WHERE person_id = ? AND service_office IS NOT NULL
        GROUP BY sales_type, service_office
        ORDER BY contract_start DESC
    ''', (person_id,)).fetchall()

    # GROUP BY (sales_type, base_name) — omitting sub_sales_type that previously
    # produced duplicate rows for the same (service, facility) pair when a bill
    # was split across sub-codes (e.g. 共同生活援助 main + 夜間).
    bills = conn.execute('''
        SELECT
            sales_type,
            MIN(sub_sales_type) AS sub_sales_type,
            base_name AS facility,
            COALESCE(SUM(credit_amount), 0) AS amount,
            COALESCE(SUM(CASE WHEN debit_account LIKE '6115%' THEN credit_amount ELSE 0 END), 0) AS insurance,
            COALESCE(SUM(CASE WHEN debit_account LIKE '6120%' THEN credit_amount ELSE 0 END), 0) AS self_pay
        FROM sales_summary
        WHERE person_id = ? AND service_month = ?
        GROUP BY sales_type, base_name
        ORDER BY amount DESC
    ''', (person_id, month)).fetchall()

    # Utilization analysis — per-service unit conversion for accuracy
    clst_ph = ','.join('?' * len(CARE_LIMIT_SALES_TYPES))
    care_detail = conn.execute(f'''
        SELECT sales_type, COALESCE(SUM(credit_amount), 0) AS yen
        FROM sales_summary
        WHERE person_id = ? AND service_month = ?
          AND sales_type IN ({clst_ph})
          AND debit_account LIKE '6115%'
        GROUP BY sales_type
    ''', (person_id, month, *CARE_LIMIT_SALES_TYPES)).fetchall()
    care_bills = {r['sales_type']: r['yen'] for r in care_detail}
    care_ins_yen = sum(care_bills.values())

    # Find residence via sales_summary.is_residential (csm removed).
    # Use the target month first; fall back to any month if not found.
    res = conn.execute('''
        SELECT service_office FROM sales_summary
        WHERE person_id = ? AND is_residential = 1
          AND service_office IS NOT NULL
          AND service_month = ?
        LIMIT 1
    ''', (person_id, month)).fetchone()
    if not res:
        res = conn.execute('''
            SELECT service_office FROM sales_summary
            WHERE person_id = ? AND is_residential = 1
              AND service_office IS NOT NULL
            ORDER BY service_month DESC LIMIT 1
        ''', (person_id,)).fetchone()
    residence = res['service_office'] if res else None

    if not residence:
        primary_fac = conn.execute('''
            SELECT base_name FROM sales_summary
            WHERE person_id = ? AND service_month = ?
            GROUP BY base_name ORDER BY SUM(credit_amount) DESC LIMIT 1
        ''', (person_id, month)).fetchone()
        if primary_fac:
            bn = primary_fac['base_name']
            for base, offices in BASE_TO_OFFICE.items():
                if bn == base:
                    residence = offices[0]
                    break

    region = OFFICE_REGION.get(residence, '鹿児島')
    est_units, avg_up = estimate_units_from_bills(care_bills, region)
    level_name, limit_units = infer_care_level_units(est_units)
    limit_yen = limit_units * avg_up if limit_units > 0 else 0

    # exceeded_amount ground-truth no longer available (person_billing_monthly removed in single-table migration)
    # Utilization is purely estimation-based, capped at 100%.
    exceeded_yen = 0
    raw_pct = round(est_units / limit_units * 100, 1) if limit_units > 0 else 0
    util_pct = min(raw_pct, 100.0)
    unused_yen = max(0, limit_yen - care_ins_yen)

    utilization = None
    if care_ins_yen > 0:
        utilization = {
            'care_ins_yen':   round(care_ins_yen),
            'inferred_level': level_name,
            'limit_yen':      round(limit_yen),
            'unused_yen':     round(unused_yen),
            'util_pct':       util_pct,
            'unit_price':     round(avg_up, 2),
            'residence':      residence,
            'exceeded_yen':   round(exceeded_yen),
        }

    conn.close()
    return jsonify({
        'info':        dict(info) if info else {},
        'services':    [dict(r) for r in services_used],
        'bills':       [dict(r) for r in bills],
        'utilization': utilization,
    })


@app.route('/api/analysis')
@login_required
def analysis():
    """
    Facility-centric analysis for executives.
    """
    month = request.args.get('month')
    conn  = get_db()

    UPSELL_PATTERNS = {
        'デイ':     ('通所介護', ['デイ', '通所', '生活介護']),
        '訪問介護': ('訪問介護',         ['訪問介護', 'ホームヘルプ']),
        '訪問看護': ('訪問看護',         ['訪問看護', '訪看']),
    }

    def has_service(used_str, keywords):
        if not used_str:
            return False
        return any(kw in stype for stype in used_str.split(',') for kw in keywords)

    # ── Per-person care insurance spending (per sales_type for unit conversion) ──
    clst_placeholders = ','.join('?' * len(CARE_LIMIT_SALES_TYPES))
    care_detail_rows = conn.execute(f'''
        SELECT person_id, sales_type, SUM(credit_amount) AS yen
        FROM sales_summary
        WHERE service_month = ?
          AND sales_type IN ({clst_placeholders})
          AND debit_account LIKE '6115%'
        GROUP BY person_id, sales_type
    ''', (month, *CARE_LIMIT_SALES_TYPES)).fetchall()
    # care_ins_by_id: person_id → insurance-only yen
    # care_bills_by_id: person_id → {sales_type: yen} (for unit conversion)
    care_ins_by_id = {}
    care_bills_by_id = {}
    for r in care_detail_rows:
        aid = r['person_id']
        care_ins_by_id[aid] = care_ins_by_id.get(aid, 0) + (r['yen'] or 0)
        if aid not in care_bills_by_id:
            care_bills_by_id[aid] = {}
        care_bills_by_id[aid][r['sales_type']] = r['yen'] or 0

    # Exceeded amounts ground-truth no longer available (person_billing_monthly removed
    # in single-table migration). Utilization falls back to estimation with 100% cap.
    pbm_exceeded_by_id = {}

    # ── Per-person service usage at residential facilities ───────────────────
    # Only UPSELL residents (住宅型有料 / サ高住) flow into 支給限度額 analysis.
    # Facilities can host mixed residential types (e.g. 東千石 has both サ高住
    # and 共同生活援助); restricting residents_raw to UPSELL sales_types prevents
    # 障害者GH residents from being counted as 自費のみ at their service_office.
    upsell_ph = ','.join('?' * len(UPSELL_RESIDENTIAL_SALES_TYPES))
    residents_raw = conn.execute(f'''
        WITH residents AS (
            SELECT person_id,
                   service_office,
                   (SELECT sales_type FROM sales_summary s2
                    WHERE s2.service_month = sales_summary.service_month
                      AND s2.person_id = sales_summary.person_id
                      AND s2.sales_type IN ({upsell_ph})
                    GROUP BY sales_type ORDER BY SUM(credit_amount) DESC LIMIT 1) AS residence_type
            FROM sales_summary
            WHERE service_month = ?
              AND sales_type IN ({upsell_ph})
              AND service_office IS NOT NULL
            GROUP BY person_id, service_office
        )
        SELECT
            r.person_id,
            nl.name AS full_name,
            r.service_office AS residence,
            r.residence_type,
            COALESCE(SUM(ss.credit_amount), 0) AS monthly_total,
            GROUP_CONCAT(DISTINCT ss.sales_type) AS used_types
        FROM residents r
        LEFT JOIN _name_lookup nl ON nl.person_id = r.person_id
        JOIN sales_summary ss ON ss.person_id = r.person_id AND ss.service_month = ?
        GROUP BY r.person_id, nl.name, r.service_office, r.residence_type
        HAVING monthly_total > 0
    ''', (*UPSELL_RESIDENTIAL_SALES_TYPES, month, *UPSELL_RESIDENTIAL_SALES_TYPES, month)).fetchall()

    # Build per-person utilization analysis
    # Primary metric: how much of their insurance limit is unused
    # Use regional unit price based on where the person lives
    person_analysis = []
    for r in residents_raw:
        aid = r['person_id']
        care_ins = care_ins_by_id.get(aid, 0)
        bills = care_bills_by_id.get(aid, {})
        region = OFFICE_REGION.get(r['residence'], '鹿児島')

        # Convert yen → units per service type, then infer level from units
        est_units, avg_up = estimate_units_from_bills(bills, region)
        level_name, limit_units = infer_care_level_units(est_units)
        # Convert back to yen for display (boss sees money)
        limit_yen = limit_units * avg_up if limit_units > 0 else 0
        # Note: care_ins includes 対象外加算 (処遇改善加算 etc.) which inflates
        # the yen total ~3-8% beyond actual limit-counted spending.
        # Cap utilization at 100% — use exceeded_amount from billing as ground truth.
        exceeded = pbm_exceeded_by_id.get(aid, 0)
        if exceeded > 0:
            # Person actually exceeds limit (confirmed by billing system)
            util_pct = 100.0
            unused_yen = 0
        else:
            raw_pct = round(est_units / limit_units * 100, 1) if limit_units > 0 else 0
            util_pct = min(raw_pct, 100.0)  # cap — 対象外加算 inflates apparent usage
            unused_yen = max(0, limit_yen - care_ins)

        person_analysis.append({
            'person_id':     aid,
            'full_name':     r['full_name'],
            'residence':     r['residence'],
            'monthly_total': r['monthly_total'],
            'care_ins_yen':  round(care_ins),
            'inferred_level': level_name,
            'limit_yen':     round(limit_yen),
            'unused_yen':    round(unused_yen),
            'util_pct':      util_pct,
            'unit_price':    round(avg_up, 2),
            'exceeded_yen':  round(exceeded),
        })

    # ── Care level distribution (aggregate person_analysis by inferred_level) ──
    care_level_counts = {}
    for pa in person_analysis:
        lvl = pa['inferred_level']
        care_level_counts[lvl] = care_level_counts.get(lvl, 0) + 1
    # Ensure canonical order
    care_level_order = ['要支援1', '要支援2', '要介護1', '要介護2', '要介護3', '要介護4', '要介護5', '自費のみ']
    care_level_distribution = {lvl: care_level_counts.get(lvl, 0) for lvl in care_level_order}
    # Include any unexpected levels
    for lvl, cnt in care_level_counts.items():
        if lvl not in care_level_distribution:
            care_level_distribution[lvl] = cnt

    # ── 1. Facility scorecard: aggregate gaps + occupancy + revenue ──────────
    # csm-based active/stopped/pending counts no longer available.
    # active = distinct residents with residential sales this month; stopped/pending = 0.
    occ_rows = conn.execute('''
        SELECT
            service_office AS office_name,
            GROUP_CONCAT(DISTINCT sales_type) AS provider_names,
            COUNT(DISTINCT person_id) AS active,
            0 AS stopped,
            0 AS pending
        FROM sales_summary
        WHERE service_month = ? AND is_residential = 1
          AND service_office IS NOT NULL
        GROUP BY service_office
        ORDER BY active DESC
    ''', (month,)).fetchall()

    # Facility revenue: for each residential office, sum ALL sales from residents living there.
    # (resident identified by is_residential row; revenue = all their bills this month)
    fac_rev = conn.execute('''
        WITH residents AS (
            SELECT DISTINCT person_id, service_office
            FROM sales_summary
            WHERE service_month = ? AND is_residential = 1
              AND service_office IS NOT NULL
        )
        SELECT
            r.service_office AS office_name,
            COALESCE(SUM(ss.credit_amount), 0) AS revenue,
            COUNT(DISTINCT r.person_id)        AS billed_residents
        FROM residents r
        JOIN sales_summary ss ON ss.person_id = r.person_id AND ss.service_month = ?
        GROUP BY r.service_office
    ''', (month, month)).fetchall()
    rev_by_office = {r['office_name']: (r['revenue'], r['billed_residents']) for r in fac_rev}

    # Aggregate per-person utilization to facility level
    # Group: insurance users (sorted by unused capacity) and self-pay
    fac_data = {}
    for pa in person_analysis:
        key = pa['residence']
        if key not in fac_data:
            fac_data[key] = {
                'total_potential': 0, 'ins_persons': [], 'self_pay_persons': [],
            }
        fd = fac_data[key]
        is_self_pay = pa['inferred_level'] == '自費のみ'
        if is_self_pay:
            fd['self_pay_persons'].append(pa)
        else:
            fd['total_potential'] += pa['unused_yen']
            fd['ins_persons'].append(pa)

    facilities = []
    for occ in occ_rows:
        office = occ['office_name']
        rev, billed = rev_by_office.get(office, (0, 0))
        fd = fac_data.get(office, {})
        active = occ['active']
        avg_rev = round(rev / active) if active else 0

        ins_persons = sorted(fd.get('ins_persons', []), key=lambda x: -x['unused_yen'])
        self_pay = sorted(fd.get('self_pay_persons', []), key=lambda x: -x['monthly_total'])
        total_pot = fd.get('total_potential', 0)

        # Facility-level avg utilization (insurance users only)
        ins_utils = [p['util_pct'] for p in ins_persons]
        avg_util = round(sum(ins_utils) / len(ins_utils), 1) if ins_utils else 0
        # Count low utilization (<50%)
        low_util_count = sum(1 for u in ins_utils if u < 50)

        # Utilization analysis only applies to 住宅型有料/サ高住 (支給限度額 system).
        # 障がい者GH, 介護付有料, 認知症GH, 特養 use institutional billing — different system.
        # provider_names now holds sales_types (post-migration), so match sales-type set.
        provider_set = set((occ['provider_names'] or '').split(','))
        has_limit_type = bool(provider_set & set(UPSELL_RESIDENTIAL_SALES_TYPES))

        up = get_unit_price(office)
        region = OFFICE_REGION.get(office, '鹿児島')
        facilities.append({
            'office_name':    office,
            'category':       _facility_category(occ['provider_names']),
            'region':         region,
            'unit_price':     up,
            'has_limit_analysis': has_limit_type,
            'active':         active,
            'stopped':        occ['stopped'],
            'pending':        occ['pending'],
            'revenue':        round(rev),
            'avg_revenue':    avg_rev,
            'total_potential': round(total_pot),
            'avg_util':       avg_util,
            'ins_count':      len(ins_persons),
            'self_pay_count': len(self_pay),
            'low_util_count': low_util_count,
            'ins_persons':    ins_persons,
            'self_pay_persons': self_pay,
            'billing_via':    BILLING_VIA.get(office),
        })

    # Split into analyzable (支給限度額) vs institutional facilities
    limit_facilities = [f for f in facilities if f['has_limit_analysis']]
    inst_facilities  = [f for f in facilities if not f['has_limit_analysis']]
    limit_facilities.sort(key=lambda x: -x['total_potential'])
    inst_facilities.sort(key=lambda x: -x['revenue'])

    total_potential = sum(f['total_potential'] for f in limit_facilities)
    total_ins_persons = sum(f['ins_count'] for f in limit_facilities)
    total_self_pay = sum(f['self_pay_count'] for f in limit_facilities)
    total_low_util = sum(f['low_util_count'] for f in limit_facilities)

    # ── 2. Category summary — revenue by service category ────────────────────
    cat_rows = conn.execute('''
        SELECT sales_type, COALESCE(SUM(credit_amount), 0) AS total,
               COUNT(DISTINCT person_id) AS customers
        FROM sales_summary WHERE service_month = ?
        GROUP BY sales_type
    ''', (month,)).fetchall()

    grand_total = sum(r['total'] for r in cat_rows) or 1
    cat_agg = {}
    for r in cat_rows:
        cat = categorize_service(r['sales_type'])
        if cat not in cat_agg:
            cat_agg[cat] = {'total': 0, 'customers': 0}
        cat_agg[cat]['total'] += r['total']
        cat_agg[cat]['customers'] += r['customers']

    categories = []
    for cat, data in cat_agg.items():
        cust = max(data['customers'], 1)
        categories.append({
            'category': cat, 'total': round(data['total']),
            'customers': data['customers'],
            'pct': round(data['total'] / grand_total * 100, 1),
            'avg_per_customer': round(data['total'] / cust),
        })
    categories.sort(key=lambda x: -x['total'])

    # ── 3. Month-over-month trend (facility-level) ───────────────────────────
    FULL_DATA_THRESHOLD = 5000
    curr_count = conn.execute(
        'SELECT COUNT(*) FROM sales_summary WHERE service_month = ?', (month,)
    ).fetchone()[0]
    prev_row = conn.execute('''
        SELECT service_month, COUNT(*) AS cnt FROM sales_summary
        WHERE service_month < ? GROUP BY service_month
        HAVING cnt >= ? ORDER BY service_month DESC LIMIT 1
    ''', (month, FULL_DATA_THRESHOLD)).fetchone()

    fac_trend = []
    trend_warning = None
    if curr_count < FULL_DATA_THRESHOLD:
        trend_warning = f'選択月（{month}）のデータが不完全のため前月比は表示できません'
    elif not prev_row:
        trend_warning = '比較できる前月データがありません'
    else:
        prev_month = prev_row['service_month']
        # Facility-level MoM: residents of each residential office (is_residential
        # this month) vs all their sales this month and prev month.
        fac_trend_raw = conn.execute('''
            WITH residents AS (
                SELECT DISTINCT person_id, service_office AS office_name
                FROM sales_summary
                WHERE service_month = ? AND is_residential = 1
                  AND service_office IS NOT NULL
            ),
            curr AS (
                SELECT r.office_name, COALESCE(SUM(ss.credit_amount),0) AS total
                FROM residents r
                JOIN sales_summary ss ON r.person_id = ss.person_id AND ss.service_month = ?
                GROUP BY r.office_name
            ),
            prev AS (
                SELECT r.office_name, COALESCE(SUM(ss.credit_amount),0) AS total
                FROM residents r
                JOIN sales_summary ss ON r.person_id = ss.person_id AND ss.service_month = ?
                GROUP BY r.office_name
            )
            SELECT c.office_name, p.total AS prev_total, c.total AS curr_total,
                   ROUND((c.total - p.total) * 100.0 / p.total, 1) AS change_pct
            FROM curr c JOIN prev p ON p.office_name = c.office_name
            WHERE p.total > 0
            ORDER BY change_pct ASC
        ''', (month, month, prev_month)).fetchall()
        fac_trend = [dict(r) | {'prev_month': prev_month} for r in fac_trend_raw]

    # ── Service-based offices (訪問系, 通所・就労, etc.) ────────────────────────
    residential_offices = {f['office_name'] for f in limit_facilities + inst_facilities}
    # Residential users this month — excluded from service-office user counts.
    residential_aids = set(r[0] for r in conn.execute(
        'SELECT DISTINCT person_id FROM sales_summary '
        'WHERE service_month = ? AND is_residential = 1', (month,)
    ).fetchall())
    # Phase D: each service_office IS a Phase-D-stripped base_name (e.g.
    # '訪問介護事業所（東光）') so joining to facility_coordinates (AppSheet's
    # physical office names like '東光') no longer matches. Group directly
    # from sales_summary and derive region via area → OFFICE_REGION.
    svc_ofc_rows = conn.execute('''
        SELECT
            service_office AS office_name,
            MIN(area) AS area,
            GROUP_CONCAT(DISTINCT sales_type) AS provider_names,
            COUNT(DISTINCT person_id) AS active
        FROM sales_summary
        WHERE service_month = ? AND service_office IS NOT NULL
        GROUP BY service_office
    ''', (month,)).fetchall()

    # Revenue for all offices — from sales_summary.service_office
    # (sales_summary_data removed in single-table migration)
    all_base_rev = conn.execute('''
        SELECT service_office AS office_name,
               COALESCE(SUM(credit_amount), 0) AS revenue
        FROM sales_summary
        WHERE service_month = ? AND service_office IS NOT NULL
        GROUP BY service_office
    ''', (month,)).fetchall()

    all_rev_map = {r['office_name']: r['revenue'] for r in all_base_rev}

    # Per-person care insurance spending (already computed above for residential)
    # care_ins_by_id is available from earlier in the function

    # Build per-office user list from sales_summary (csm removed).
    svc_ofc_users = conn.execute('''
        SELECT DISTINCT service_office AS office_name, person_id
        FROM sales_summary
        WHERE service_month = ? AND service_office IS NOT NULL
    ''', (month,)).fetchall()

    # Group users by office
    office_users = {}
    for r in svc_ofc_users:
        office_users.setdefault(r['office_name'], set()).add(r['person_id'])

    svc_facilities = []
    for r in svc_ofc_rows:
        office = r['office_name']
        if not office or office in residential_offices:
            continue
        providers = r['provider_names'] or ''
        otype = _office_type(providers)
        if otype in ('居住系', '事業・本社'):
            continue
        # Exclude residents already counted in 居宅系 section
        svc_only_users = office_users.get(office, set()) - residential_aids
        active = len(svc_only_users)
        if active == 0:
            continue
        billing_rev = round(all_rev_map.get(office, 0))
        bv = BILLING_VIA.get(office)

        # csm-based attributed revenue calculation removed.
        # Revenue = direct sales_summary.service_office totals only.
        rev = billing_rev if billing_rev > 0 else 0
        avg_rev = round(rev / active) if active and rev else 0

        # Region via area column (Phase D). Falls back to '鹿児島' for offices
        # without a registered region in OFFICE_REGION.
        ofc_region = OFFICE_REGION.get(r['area']) or OFFICE_REGION.get(office) or '鹿児島'
        users = svc_only_users
        total_pot = 0
        ins_count = 0
        low_util = 0
        utils = []
        for uid in users:
            ci = care_ins_by_id.get(uid, 0)
            if ci <= 0:
                continue
            bills = care_bills_by_id.get(uid, {})
            eu, aup = estimate_units_from_bills(bills, ofc_region)
            lv, lim_u = infer_care_level_units(eu)
            lim_yen = lim_u * aup if lim_u > 0 else 0
            raw_ut = round(eu / lim_u * 100, 1) if lim_u > 0 else 0
            exc = pbm_exceeded_by_id.get(uid, 0)
            if exc > 0:
                ut = 100.0
                unused = 0
            else:
                ut = min(raw_ut, 100.0)
                unused = max(0, lim_yen - ci)
            total_pot += unused
            ins_count += 1
            utils.append(ut)
            if ut < 50:
                low_util += 1

        avg_util = round(sum(utils) / len(utils), 1) if utils else 0

        svc_facilities.append({
            'office_name':    office,
            'category':       _facility_category(providers),
            'type':           otype,
            'region':         ofc_region,
            'active':         active,
            'revenue':        rev,
            'avg_revenue':    avg_rev,
            'ins_count':      ins_count,
            'avg_util':       avg_util,
            'low_util_count': low_util,
            'billing_via':    BILLING_VIA.get(office),
        })
    svc_facilities.sort(key=lambda x: -x['revenue'])

    # True total unused capacity (deduplicated per person, counted once)
    true_total_unused = 0
    for aid, ci in care_ins_by_id.items():
        if ci <= 0:
            continue
        if pbm_exceeded_by_id.get(aid, 0) > 0:
            continue  # already at/over limit, no unused capacity
        bills = care_bills_by_id.get(aid, {})
        eu, aup = estimate_units_from_bills(bills, '鹿児島')
        _, lim_u = infer_care_level_units(eu)
        lim_yen = lim_u * aup if lim_u > 0 else 0
        true_total_unused += max(0, lim_yen - ci)
    true_total_unused = round(true_total_unused)

    # Pipeline (検討中 leads) and tenure distribution depend on csm.status and
    # csm.contract_start, which are not available in single-table migration.
    # Keys preserved for frontend compat with empty values.
    pipeline = []
    total_pipeline = 0
    tenure_dist = [{'years': k, 'count': 0} for k in ('0-1', '1-3', '3-5', '5-10', '10+')]
    avg_tenure = 0

    conn.close()
    return jsonify({
        'facilities':         limit_facilities,
        'inst_facilities':    inst_facilities,
        'svc_facilities':     svc_facilities,
        'total_potential':    true_total_unused,
        'residential_potential': total_potential,
        'total_ins_persons':  total_ins_persons,
        'total_self_pay':     total_self_pay,
        'total_low_util':     total_low_util,
        'total_ins_all':      len([v for v in care_ins_by_id.values() if v > 0]),
        'categories':        categories,
        'fac_trend':         fac_trend,
        'trend_warning':     trend_warning,
        'pipeline':          pipeline,
        'total_pipeline':    total_pipeline,
        'tenure_distribution': tenure_dist,
        'avg_tenure':        avg_tenure,
        'care_level_distribution': care_level_distribution,
    })


@app.route('/api/cross-sell')
@login_required
def cross_sell():
    """Cross-sell opportunities: residential customers who CAN use external services.
    Only 住宅型有料老人ホーム and サービス付き高齢者向け住宅 residents can freely
    add external services (訪問介護, 訪問看護, 通所介護).
    介護付有料 (特定施設), 認知症GH, 特養, 障害者GH cannot — excluded here."""
    month = request.args.get('month')
    conn = get_db()

    # Only these provider types allow external service addition
    # Cross-sell eligibility: 住宅型有料老人ホーム / サ高住 residents (identified
    # via sales_type match in single-table migration). csm-based listing removed.
    # sales_summary can identify 88% of residents (verified empirically); missed
    # residents are mostly non-billed registered users which frontend doesn't need.
    res_rows = conn.execute('''
        SELECT DISTINCT ss.person_id, nl.name AS full_name,
               ss.service_office AS residence
        FROM sales_summary ss
        LEFT JOIN _name_lookup nl ON nl.person_id = ss.person_id
        WHERE ss.service_month = ? AND ss.service_office IS NOT NULL
          AND (ss.sales_type LIKE '%有料老人ホーム%' OR ss.sales_type LIKE '%サ高住%'
               OR ss.sales_type LIKE '%サービス付き高齢者向け住宅%')
    ''', (month,)).fetchall()

    all_customers = {}
    for r in res_rows:
        aid = r['person_id']
        if aid not in all_customers:
            all_customers[aid] = {
                'person_id': aid, 'full_name': r['full_name'],
                'residence': r['residence'], 'services': set(),
            }

    if all_customers:
        aid_list = list(all_customers.keys())
        aid_ph = ','.join('?' * len(aid_list))
        # Services = sales_types billed for this user this month (replaces csm provider list)
        svc_rows = conn.execute(f'''
            SELECT DISTINCT person_id, sales_type FROM sales_summary
            WHERE service_month = ? AND person_id IN ({aid_ph})
        ''', (month, *aid_list)).fetchall()
        for sr in svc_rows:
            if sr['person_id'] in all_customers:
                all_customers[sr['person_id']]['services'].add(sr['sales_type'])

        bill_rows = conn.execute(f'''
            SELECT person_id, COALESCE(SUM(credit_amount),0) AS total
            FROM sales_summary WHERE service_month = ? AND person_id IN ({aid_ph})
            GROUP BY person_id
        ''', (month, *aid_list)).fetchall()
        billing = {r['person_id']: r['total'] for r in bill_rows}
    else:
        billing = {}

    # Cross-sell checks:
    # 訪問介護: requires 要介護認定 + ケアプラン — manageable, ACG can arrange
    # 訪問看護: requires 主治医の訪問看護指示書 — needs doctor's order, harder to cross-sell
    # 通所介護: requires 要介護認定 + ケアプラン — manageable
    # We show all as opportunities but label 訪問看護 differently (要主治医指示)
    CHECKS = {
        '訪問介護': ['訪問介護'],
        '訪問看護': ['訪問看護'],
        '通所介護': ['通所介護', '地域密着型通所介護', '生活介護'],
    }

    def has_svc(svcs, keywords):
        return any(kw in s for s in svcs for kw in keywords)

    targets = []
    single = no_home = no_nursing = no_day = 0
    density = {}
    for aid, info in all_customers.items():
        svcs = info['services']
        n = len(svcs)
        density[n] = density.get(n, 0) + 1
        if n <= 1:
            single += 1
        missing = []
        if not has_svc(svcs, CHECKS['訪問介護']):
            no_home += 1; missing.append('訪問介護')
        if not has_svc(svcs, CHECKS['通所介護']):
            no_day += 1; missing.append('通所介護')
        # 訪問看護 requires doctor's order — still show but separate
        if not has_svc(svcs, CHECKS['訪問看護']):
            no_nursing += 1; missing.append('訪問看護（要主治医指示）')
        if missing:
            targets.append({
                'person_id': aid, 'full_name': info['full_name'],
                'residence': info['residence'], 'missing': missing,
                'service_count': n, 'monthly_total': billing.get(aid, 0),
            })

    targets.sort(key=lambda x: (x['service_count'], -x['monthly_total']))
    conn.close()
    return jsonify({
        'summary': {
            'total_residential': len(all_customers), 'single_service': single,
            'no_home_care': no_home, 'no_nursing': no_nursing, 'no_day': no_day,
        },
        'targets': targets,
        'service_density': sorted([{'count': k, 'customers': v} for k, v in density.items()], key=lambda x: x['count']),
    })


@app.route('/api/trend-history')
@login_required
def trend_history():
    """Monthly total billing per service category + facility — last 16 months."""
    conn = get_db()
    # Category-level
    cat_rows = conn.execute('''
        SELECT service_month, sales_type,
               COALESCE(SUM(credit_amount), 0) AS total
        FROM sales_summary
        GROUP BY service_month, sales_type ORDER BY service_month
    ''').fetchall()

    # Facility-level from sales_summary.service_office
    # (sales_summary_data removed in single-table migration)
    fac_rows = conn.execute('''
        SELECT service_month, service_office AS office_name,
               COALESCE(SUM(credit_amount), 0) AS total
        FROM sales_summary
        WHERE service_office IS NOT NULL
        GROUP BY service_month, service_office ORDER BY service_month
    ''').fetchall()

    conn.close()

    fac_result = [{'service_month': r['service_month'],
                   'office_name': r['office_name'],
                   'total': round(r['total'])} for r in fac_rows]

    return jsonify({
        'categories': [dict(r) | {'category': categorize_service(r['sales_type'])} for r in cat_rows],
        'facilities': fac_result,
    })



@app.route('/api/map')
@login_required
def map_data():
    """Facility locations with revenue data for map display.

    Phase D: aggregates by the `area` column that snapshot.py populated
    from display_for_base_name(). Multiple base_names at one physical
    address (e.g. 東光_訪問看護 + 東光_訪問介護 + 東光_居宅) share the
    same area and render as one pin.
    """
    month = request.args.get('month')
    conn  = get_db()

    # Coordinates keyed by physical office_name from facility_coordinates.
    coords = conn.execute('''
        SELECT office_name, lat, lng, address, region FROM facility_coordinates
    ''').fetchall()
    coord_map = {r['office_name']: r for r in coords}

    rev_map = {}
    rev_cat_map = {}
    providers_set = {}
    active_ids = {}

    for r in conn.execute('''
        SELECT area, sales_type, person_id,
               COALESCE(SUM(credit_amount), 0) AS amount
        FROM sales_summary
        WHERE service_month = ? AND area IS NOT NULL
        GROUP BY area, sales_type, person_id
    ''', (month,)).fetchall():
        grp = r['area']
        if not grp: continue
        amt = round(r['amount'])
        rev_map[grp] = rev_map.get(grp, 0) + amt
        cat = categorize_service(r['sales_type'])
        rev_cat_map.setdefault(grp, {})[cat] = rev_cat_map.setdefault(grp, {}).get(cat, 0) + amt
        providers_set.setdefault(grp, set()).add(r['sales_type'])
        if r['person_id']:
            active_ids.setdefault(grp, set()).add(r['person_id'])

    result = []
    for office, revenue in rev_map.items():
        c = coord_map.get(office)
        if not c or not c['lat'] or not c['lng']:
            continue
        active = len(active_ids.get(office, set()))
        providers = ','.join(sorted(providers_set.get(office, set())))
        cat = _facility_category(providers)
        otype = _office_type(providers)
        avg_rev = round(revenue / active) if active and revenue else 0

        result.append({
            'office_name': office,
            'lat': c['lat'],
            'lng': c['lng'],
            'address': c['address'],
            'region': c['region'],
            'category': cat,
            'type': otype,
            'active': active,
            'pending': 0,
            'revenue': revenue,
            'avg_revenue': avg_rev,
            'billing_via': BILLING_VIA.get(office),
            'rev_by_cat': rev_cat_map.get(office, {}),
        })

    conn.close()
    return jsonify(result)


@app.route('/api/facility-trend')
@login_required
def facility_trend():
    """Last 6 months of revenue with per-category breakdown.

    Returns two parallel breakdowns per month so both 拠点別請求 and 居住者合計
    modal views can show faithful trend legends:
      - by_cat           — billing attributed to this service_office (same
                           data as 拠点別請求 shows in the main view)
      - by_cat_resident  — ALL bills (any service_office) of users who reside
                           at this facility that month (is_residential sales
                           at this facility). Matches 居住者合計's rev_by_cat.
    """
    raw_name = request.args.get('name') or ''
    # Compound names from resident view (e.g. '有料東千石') split the residential
    # slice; parse to (category, physical_name). Billing still uses physical.
    resident_cat, name = parse_compound_facility_name(raw_name)
    conn = get_db()

    # --- Billing-based trend (拠点別請求) ---
    # For compound names, filter bills by the billing-split rule so the trend
    # matches the split's row in the main 拠点別請求 list — per month detect
    # base_name primaries; bill's own sales_type decides for residential.
    # sub_sales_type is added so the facility-detail modal's trend chart can
    # draw a line per sub (the same rows it shows in 請求内訳).
    rows = conn.execute('''
        SELECT service_month, sales_type,
               COALESCE(NULLIF(sub_sales_type, ''), sales_type) AS sub_key,
               base_name, category, is_residential,
               COALESCE(SUM(credit_amount), 0) AS amount
        FROM sales_summary
        WHERE service_office = ?
        GROUP BY service_month, sales_type, sub_key, base_name, category, is_residential
        ORDER BY service_month
    ''', (name,)).fetchall()

    def _split_match_month(m, base_name, sales_type, is_residential, category):
        if not resident_cat:
            return True
        # Per-month base_name primary cache
        if m not in _bn_primary_by_month:
            d = {}
            for rr in conn.execute('''
                SELECT base_name,
                       CASE WHEN sales_type LIKE '%サ高住%' THEN 'サ高住' ELSE category END AS dc,
                       COALESCE(SUM(credit_amount), 0) AS amt
                FROM sales_summary
                WHERE service_month = ? AND service_office = ? AND is_residential = 1
                GROUP BY base_name, dc
            ''', (m, name)).fetchall():
                prev = d.get(rr['base_name'])
                if prev is None or rr['amt'] > prev[1]:
                    d[rr['base_name']] = (rr['dc'], rr['amt'])
            _bn_primary_by_month[m] = d
            # Also dominant
            cats = {}
            for rr in conn.execute('''
                SELECT CASE WHEN sales_type LIKE '%サ高住%' THEN 'サ高住' ELSE category END AS dc,
                       COALESCE(SUM(credit_amount), 0) AS amt
                FROM sales_summary
                WHERE service_month = ? AND service_office = ? AND is_residential = 1
                GROUP BY dc
            ''', (m, name)).fetchall():
                cats[rr['dc']] = rr['amt']
            _dominant_by_month[m] = max(cats, key=cats.get) if cats else None
        if is_residential:
            dc = 'サ高住' if sales_type and 'サ高住' in sales_type else category
        else:
            bp = _bn_primary_by_month[m].get(base_name)
            dc = bp[0] if bp else _dominant_by_month.get(m)
        return dc == resident_cat

    import re as _re_trend
    _strip_prefix = lambda s: _re_trend.sub(r'^\d{3,5}_', '', s) if s else s

    _bn_primary_by_month = {}
    _dominant_by_month = {}
    by_month = {}
    for r in rows:
        if not _split_match_month(r['service_month'], r['base_name'], r['sales_type'],
                                  r['is_residential'], r['category']):
            continue
        m = r['service_month']
        cat = categorize_service(r['sales_type'])
        sub_display = _strip_prefix(r['sub_key'])
        if m not in by_month:
            by_month[m] = {'total': 0, 'by_cat': {}, 'by_sub': {}, 'by_cat_resident': {}, 'residents': 0}
        by_month[m]['total'] += r['amount']
        by_month[m]['by_cat'][cat] = by_month[m]['by_cat'].get(cat, 0) + r['amount']
        by_month[m]['by_sub'][sub_display] = by_month[m]['by_sub'].get(sub_display, 0) + r['amount']

    # --- Resident-based trend (居住者合計) ---
    # Identify residents of this facility per month, then aggregate ALL their
    # bills (any service_office) for that same month by display category.
    # If compound name, restrict residents to the requested category.
    cat_clause = " AND (CASE WHEN sales_type LIKE '%サ高住%' THEN 'サ高住' ELSE category END) = ?" if resident_cat else ""
    cat_params = (resident_cat,) if resident_cat else ()
    res_ids = conn.execute(f'''
        SELECT service_month, person_id
        FROM sales_summary
        WHERE service_office = ? AND is_residential = 1
          AND person_id IS NOT NULL AND person_id != 'FACILITY'{cat_clause}
        GROUP BY service_month, person_id
    ''', (name, *cat_params)).fetchall()

    residents_by_month = {}
    for r in res_ids:
        residents_by_month.setdefault(r['service_month'], set()).add(r['person_id'])

    # When compound, exclude cross-residential bills (same rule as the
    # facilities() and facility_detail() queries — see their comments).
    trend_other_res_clause = (
        " AND NOT (is_residential = 1 AND "
        "(CASE WHEN sales_type LIKE '%サ高住%' THEN 'サ高住' ELSE category END) != ?)"
        if resident_cat else ""
    )
    trend_other_res_params = (resident_cat,) if resident_cat else ()
    for m, aids in residents_by_month.items():
        if not aids:
            continue
        aid_list = list(aids)
        aid_ph = ','.join('?' * len(aid_list))
        bill_rows = conn.execute(f'''
            SELECT CASE WHEN sales_type LIKE '%サ高住%' THEN 'サ高住' ELSE category END AS disp_cat,
                   COALESCE(SUM(credit_amount), 0) AS amt
            FROM sales_summary
            WHERE service_month = ? AND person_id IN ({aid_ph})
              AND category IS NOT NULL{trend_other_res_clause}
            GROUP BY disp_cat
        ''', (m, *aid_list, *trend_other_res_params)).fetchall()
        if m not in by_month:
            by_month[m] = {'total': 0, 'by_cat': {}, 'by_sub': {}, 'by_cat_resident': {}, 'residents': 0}
        for r in bill_rows:
            if r['amt']:
                by_month[m]['by_cat_resident'][r['disp_cat']] = round(r['amt'])
        by_month[m]['residents'] = len(aids)

    months = sorted(by_month.keys())[-6:]
    result = [{'service_month': m,
               'total': round(by_month[m]['total']),
               'by_cat': {k: round(v) for k, v in by_month[m]['by_cat'].items()},
               'by_sub': {k: round(v) for k, v in by_month[m]['by_sub'].items()},
               'by_cat_resident': by_month[m]['by_cat_resident'],
               'residents': by_month[m]['residents']}
              for m in months]

    conn.close()
    return jsonify(result)


@app.route('/api/alerts')
@login_required
def alerts():
    """MoM changes: revenue swings, new/lost users."""
    month = request.args.get('month')
    conn = get_db()

    # Find previous month from sales_summary (sales_summary_data removed)
    prev_row = conn.execute('''
        SELECT service_month FROM sales_summary
        WHERE service_month < ? GROUP BY service_month
        ORDER BY service_month DESC LIMIT 1
    ''', (month,)).fetchone()

    if not prev_row:
        conn.close()
        return jsonify({'summary': {}, 'details': []})

    pm = prev_row['service_month']

    # Revenue by office from sales_summary.service_office
    def office_rev(m):
        rows = conn.execute('''
            SELECT service_office AS office_name,
                   COALESCE(SUM(credit_amount),0) AS rev
            FROM sales_summary
            WHERE service_month = ? AND service_office IS NOT NULL
            GROUP BY service_office
        ''', (m,)).fetchall()
        return {r['office_name']: r['rev'] for r in rows}

    curr_rev = office_rev(month)
    prev_rev = office_rev(pm)

    details = []
    up_count = 0
    down_count = 0
    for ofc in set(curr_rev) | set(prev_rev):
        c = curr_rev.get(ofc, 0)
        p = prev_rev.get(ofc, 0)
        if p == 0:
            continue
        pct = round((c - p) / p * 100, 1)
        if abs(pct) >= 15:
            dtype = 'up' if pct > 0 else 'down'
            if pct > 0:
                up_count += 1
            else:
                down_count += 1
            details.append({'type': dtype, 'name': ofc, 'prev': round(p), 'curr': round(c), 'change_pct': pct})

    # New / lost users
    curr_users = set(r['person_id'] for r in conn.execute(
        'SELECT DISTINCT person_id FROM sales_summary WHERE service_month = ?', (month,)
    ))
    prev_users = set(r['person_id'] for r in conn.execute(
        'SELECT DISTINCT person_id FROM sales_summary WHERE service_month = ?', (pm,)
    ))
    new_users = curr_users - prev_users
    lost_users = prev_users - curr_users

    details.sort(key=lambda x: x.get('change_pct', 0))

    conn.close()
    return jsonify({
        'prev_month': pm,
        'summary': {
            'up_facilities': up_count,
            'down_facilities': down_count,
            'new_users': len(new_users),
            'lost_users': len(lost_users),
        },
        'details': details,
    })


# ── Card View (sales_fmt_data source) ────────────────────────────────────────
#
# The main 拠点 tab aggregates sales_summary (user-linked billing). The card
# view tab uses sales_fmt_data — the underlying accounting-journal table —
# which captures every posted line including facility-level items (meal,
# laundry, drink sales, etc.) that sales_summary may not carry for every
# facility. Totals match sales_summary within 0.2%; the card view is the
# source of truth for "what did this physical facility book this month".
#
# Aggregation key: (resolved_facility, credit_sub_account).
#   · base_name → resolved_facility via config.BASE_SALES_TYPE_ROUTE
#     (compound split for 0048_うらら拠点 → うらら1 / うらら2 by keyword)
#     falling back to config.display_for_base_name() for normal facilities.
#   · credit_sub_account already uses the "CODE_ラベル" form that matches
#     config.SALES_TYPE_CATEGORY / categorize_service() out of the box.
# Insurance vs. self-pay split comes from the debit_account prefix
# (config.debit_side).

from config import (
    BASE_SALES_TYPE_ROUTE,
    display_for_base_name,
    _NAME_OVERRIDES,
    categorize_service,
    debit_side,
)


def _resolve_card_facility(base_name, sub_account):
    """sales_fmt_data base_name + credit_sub_account → canonical facility name.

    Handles 0048_うらら拠点 compound split via BASE_SALES_TYPE_ROUTE keywords.
    Falls back to display_for_base_name for normal facilities."""
    route = BASE_SALES_TYPE_ROUTE.get(base_name)
    if route:
        for keyword, office in route:
            if keyword in (sub_account or ''):
                return office  # 'うらら1' / 'うらら2' — canonical, no override
    return display_for_base_name(base_name)[0]


def _svc_label(sub_account):
    """credit_sub_account → human-readable service label.

    For descriptive labels (e.g. '1110_通所介護（デイサービス）') strip the
    leading CODE_ prefix. For generic catch-all labels (e.g. '2299_その他')
    keep the code so rows stay unambiguous — two separate accounts both
    called 'その他' would otherwise look identical in the UI."""
    if not sub_account:
        return 'その他'
    import re
    m = re.match(r'^([\d_]+)_(.+)$', sub_account)
    if not m:
        return sub_account
    code, label = m.group(1), m.group(2)
    if label in ('その他', 'その他収入', 'その他 '):
        return f'{code} {label.strip()}'
    return label


def _svc_code(sub_account):
    """credit_sub_account → leading CODE fragment (for stable sort/keying)."""
    if not sub_account:
        return ''
    import re
    m = re.match(r'^([\d_]+)_', sub_account)
    return m.group(1) if m else ''


# Note: the "利用者" list in the card-detail modal is sourced directly from
# sales_fmt_data's person_id stream WITHOUT any keyword-based filtering.
# Municipal / institutional counter-parties (e.g. '訪問介護 福岡県',
# 'ダイナミックベンディングネットワーク株式会社') appear as their own rows.
# This matches the accounting journal reality: insurance is billed to the
# municipality, so individual resident rows naturally show ¥0 insurance and
# the municipality row carries the insurance portion. Showing both preserves
# totals consistency with the 請求内訳 table above.

# Classification keywords. The weak set (県/市/区 etc.) is only used in
# combination with service keywords, because personal names routinely
# contain 市/町/村 as surname characters (e.g. 今市, 市川, 中村, 上村).
# Strong keywords are corporate / institutional markers that effectively
# never appear in a resident's name.
_FACILITY_STRONG_KEYWORDS = (
    '株式会社', '有限会社', '合同会社', 'ネットワーク',
    '事業所', '施設', 'ステーション',
    '特別養護', '有料老人', 'サービス付', 'グループホーム',
    '地域密着', '介護支援',
    'デイサービス', 'デイ・', 'ホーム',
)
_MUNICIPAL_SERVICE_KEYWORDS = (
    '訪問介護', '訪問看護', '居宅介護', '通所介護',
    '移動支援', '地方自治体', '施設介護', '重度訪問', '特別養護',
    '障害福祉', '就労継続', '就労選択',
)
_MUNICIPAL_LOCATION_KEYWORDS = ('県', '区', '町村')


def _is_facility_customer(customer_name):
    if not customer_name:
        return False
    for kw in _FACILITY_STRONG_KEYWORDS:
        if kw in customer_name:
            return True
    # 県/市/区 alone is not enough (surnames like 今市和宣 contain 市).
    # Require a service-related keyword to also be present.
    has_service = any(k in customer_name for k in _MUNICIPAL_SERVICE_KEYWORDS)
    if has_service:
        if any(k in customer_name for k in _MUNICIPAL_LOCATION_KEYWORDS):
            return True
        # '市' is too common in personal names — only treat as municipal if
        # preceded by a prefecture-style segment (e.g. "…福岡市", "…鹿児島市").
        if '市' in customer_name:
            return True
    return False


@app.route('/api/facility-cards')
@login_required
def facility_cards():
    """Per-facility card data for the given month, sourced from sales_fmt_data.

    Returns a list of {name, total, categories, services[]} sorted by total desc.
    Services within each facility are sorted by amount desc."""
    month = request.args.get('month')
    if not month:
        return jsonify([])
    conn = get_db()
    rows = conn.execute('''
        SELECT base_name, credit_sub_account AS sub,
               COALESCE(SUM(credit_amount), 0) AS amount
        FROM sales_fmt_data
        WHERE service_month = ?
          AND base_name IS NOT NULL AND base_name != ''
        GROUP BY base_name, credit_sub_account
    ''', (month,)).fetchall()
    conn.close()

    from collections import defaultdict
    facs = defaultdict(lambda: {'total': 0, 'services': {}, 'cats': set()})
    for r in rows:
        fac_name = _resolve_card_facility(r['base_name'], r['sub'])
        svc_name = _svc_label(r['sub'])
        code = _svc_code(r['sub'])
        cat = categorize_service(r['sub']) if r['sub'] else 'その他'
        amt = r['amount'] or 0
        rec = facs[fac_name]
        rec['total'] += amt
        rec['cats'].add(cat)
        key = (cat, svc_name)
        svc = rec['services'].setdefault(key, {
            'category': cat, 'service_name': svc_name, 'code': code, 'amount': 0,
        })
        svc['amount'] += amt

    out = []
    for name, rec in facs.items():
        out.append({
            'name': name,
            'total': round(rec['total']),
            'categories': sorted(rec['cats']),
            'services': sorted(
                ({**s, 'amount': round(s['amount'])} for s in rec['services'].values()),
                key=lambda s: -s['amount'],
            ),
        })
    out.sort(key=lambda f: -f['total'])
    return jsonify(out)


@app.route('/api/facility-cards-detail')
@login_required
def facility_cards_detail():
    """Full detail for a single facility (card-view drill-down), sales_fmt_data.

    Returns {name, total, billing[], users[], trend[]}:
      billing: per-service rows with insurance/self_pay split via debit_side
      users:   per-person_id rows with totals and splits
      trend:   up-to 12 months of this facility's total, oldest → newest

    `name` request arg is the canonical facility name (as returned by
    facility_cards, e.g. '宇美拠点', 'うらら1')."""
    month = request.args.get('month')
    name = request.args.get('name')
    if not month or not name:
        return jsonify({'name': name or '', 'total': 0, 'billing': [], 'users': [], 'trend': []})
    conn = get_db()

    # Pull all fmt rows and filter to this facility in Python (compound split
    # logic lives in _resolve_card_facility, which needs sub_account).
    rows = conn.execute('''
        SELECT base_name, credit_sub_account AS sub,
               debit_account AS deb, person_id, customer_name,
               COALESCE(credit_amount, 0) AS amount
        FROM sales_fmt_data
        WHERE service_month = ?
          AND base_name IS NOT NULL AND base_name != ''
    ''', (month,)).fetchall()

    # name-lookup for display (sales_summary._name_lookup is the canonical source)
    name_by_aid = {}
    try:
        for r in conn.execute('SELECT person_id, name FROM _name_lookup').fetchall():
            name_by_aid[r['person_id']] = r['name']
    except Exception:
        pass

    from collections import defaultdict

    # ── Pre-scan: resolve each person_id to a single (name, is_facility)
    # classification for the whole facility's transactions. Individual rows
    # in sales_fmt_data may or may not carry customer_name on every line
    # (e.g. only the first journal leg of a series has the counter-party),
    # so we merge information across all rows of the same aid and pick the
    # best non-empty name + its facility classification. This guarantees a
    # municipal/vendor person_id never drifts into the 個人 pool just because
    # one of its rows had a blank customer_name.
    aid_info = {}  # aid → {'name': str, 'is_facility': bool}
    for r in rows:
        if _resolve_card_facility(r['base_name'], r['sub']) != name:
            continue
        aid = r['person_id']
        if not aid:
            continue
        existing = aid_info.get(aid)
        if existing and existing['name']:
            continue  # already resolved with a non-empty name
        candidate_name = (name_by_aid.get(aid) or '').strip() or (r['customer_name'] or '').strip()
        if not candidate_name:
            continue
        aid_info[aid] = {
            'name': candidate_name,
            'is_facility': _is_facility_customer(candidate_name),
        }

    billing_map = {}
    # Per-billing-row customer breakdown. Outer key = (category, service_label);
    # inner key = customer display name OR the sentinel '__residents__' to
    # collect all individual-resident rows into one pooled entry per billing
    # row. Facility / municipal / vendor customers get their own entries.
    billing_cust_map = defaultdict(lambda: defaultdict(lambda: {
        'amount': 0, 'insurance': 0, 'self_pay': 0,
        'is_facility': False, 'aids': set(),
    }))
    users_map = defaultdict(lambda: {
        'amount': 0, 'insurance': 0, 'self_pay': 0,
        'full_name': None,
        # categories this customer's transactions touched at this facility.
        # Keyed by category → per-category (amount, insurance, self_pay) so
        # the card-detail chip filter can mute a category and have the user
        # row's amounts recompute to only the surviving categories.
        'cat_amounts': defaultdict(lambda: {'amount': 0, 'insurance': 0, 'self_pay': 0}),
    })
    total = 0
    for r in rows:
        fac_name = _resolve_card_facility(r['base_name'], r['sub'])
        if fac_name != name:
            continue
        sub = r['sub']
        svc_label = _svc_label(sub)
        cat = categorize_service(sub) if sub else 'その他'
        amt = r['amount'] or 0
        side = debit_side(r['deb'])
        total += amt

        bkey = (cat, svc_label)
        bill = billing_map.setdefault(bkey, {
            'category': cat, 'service_name': svc_label, 'code': _svc_code(sub),
            'amount': 0, 'insurance': 0, 'self_pay': 0,
        })
        bill['amount'] += amt
        bill[side] += amt

        # Classify customer for the billing-row breakdown using the aid's
        # pre-resolved name / facility flag (consistent across all of this
        # aid's rows).
        aid = r['person_id']
        info = aid_info.get(aid) if aid else None
        if info and info['is_facility']:
            cust_key = info['name']
            entry = billing_cust_map[bkey][cust_key]
            entry['is_facility'] = True
        elif info:
            cust_key = '__residents__'
            entry = billing_cust_map[bkey][cust_key]
            entry['is_facility'] = False
        else:
            # No aid or no name info → fall back to the row's own
            # customer_name classification.
            raw = (r['customer_name'] or '').strip()
            if _is_facility_customer(raw):
                cust_key = raw or '（名称不明）'
                entry = billing_cust_map[bkey][cust_key]
                entry['is_facility'] = True
            else:
                cust_key = '__residents__'
                entry = billing_cust_map[bkey][cust_key]
                entry['is_facility'] = False
        # Only count an aid in the pool if it actually contributed revenue
        # in this row. Journal rows with amt==0 shouldn't inflate the count.
        if aid and amt != 0:
            entry['aids'].add(aid)
        entry['amount'] += amt
        entry[side] += amt

        if aid:
            u = users_map[aid]
            u['amount'] += amt
            u[side] += amt
            u['cat_amounts'][cat]['amount'] += amt
            u['cat_amounts'][cat][side] += amt
            if u['full_name'] is None and info:
                u['full_name'] = info['name']

    # ── Synthesize pseudo-user entries for aidless facility customers ──
    # Some sales_fmt_data rows have customer_name set (e.g. 'デモ介護
    # ステーション') but no person_id — typically inter-facility / inter-
    # department settlement journal entries. Without this post-pass the
    # 請求詳細 (user list) wouldn't include these counter-parties, so
    # muting the customer in 請求元 couldn't decrement the detail totals.
    for bkey, custs in billing_cust_map.items():
        cat_key = bkey[0]
        for cust_key, data in custs.items():
            if not data['is_facility']:
                continue
            if data['aids']:
                continue  # real person_id already tracked in users_map
            pseudo_aid = f'__facility__{cust_key}'
            u = users_map[pseudo_aid]
            u['amount'] += data['amount']
            u['insurance'] += data['insurance']
            u['self_pay'] += data['self_pay']
            u['cat_amounts'][cat_key]['amount'] += data['amount']
            u['cat_amounts'][cat_key]['insurance'] += data['insurance']
            u['cat_amounts'][cat_key]['self_pay'] += data['self_pay']
            if u['full_name'] is None:
                u['full_name'] = cust_key
            # Put the pseudo aid in the billing entry's aids set so the
            # customer shows count=1 (one synthetic counter-party).
            data['aids'].add(pseudo_aid)

    # trend: up to 12 months of totals for this facility
    # pull per-month base_name×sub sums, resolve to facility, sum
    trend_rows = conn.execute('''
        SELECT service_month, base_name, credit_sub_account AS sub,
               COALESCE(SUM(credit_amount), 0) AS amt
        FROM sales_fmt_data
        WHERE base_name IS NOT NULL AND base_name != ''
          AND service_month <= ?
        GROUP BY service_month, base_name, credit_sub_account
        ORDER BY service_month
    ''', (month,)).fetchall()
    by_month = defaultdict(float)
    for r in trend_rows:
        if _resolve_card_facility(r['base_name'], r['sub']) == name:
            by_month[r['service_month']] += r['amt'] or 0
    trend = [{'month': m, 'total': round(v)} for m, v in sorted(by_month.items())]
    if len(trend) > 12:
        trend = trend[-12:]

    conn.close()

    # Drop billing rows that net to zero — these are usually offsetting
    # journal entries (e.g. 1270_地方自治体独自事業 reversal where
    # insurance + self_pay cancel), not real facility revenue. Attach each
    # row's customer breakdown so the UI can show "請求元" directly.
    def _format_customers(bkey):
        out = []
        cust_entries = billing_cust_map.get(bkey) or {}
        for cust_key, data in cust_entries.items():
            if round(data['amount']) == 0:
                continue
            # Include aids list so the range-merge path can union across
            # months and report an accurate unique-count (otherwise a
            # range query would over- or under-state the pool headcount).
            aids = sorted(data['aids'])
            if cust_key == '__residents__':
                n = len(aids)
                # Use 「個人」 rather than 「住民」 — non-residential facilities
                # (訪問介護, 就労, 居宅 等) have users/clients who don't live
                # there, so 「住民」 would be misleading.
                out.append({
                    'name': f'個人 {n}名' if n else '個人',
                    'amount': round(data['amount']),
                    'insurance': round(data['insurance']),
                    'self_pay': round(data['self_pay']),
                    'is_facility': False,
                    'count': n,
                    'aids': aids,
                })
            else:
                out.append({
                    'name': cust_key,
                    'amount': round(data['amount']),
                    'insurance': round(data['insurance']),
                    'self_pay': round(data['self_pay']),
                    'is_facility': True,
                    'count': len(aids),
                    'aids': aids,
                })
        out.sort(key=lambda c: -c['amount'])
        return out

    billing = []
    for bkey, b in billing_map.items():
        if round(b['amount']) == 0:
            continue
        billing.append({
            **b,
            'amount': round(b['amount']),
            'insurance': round(b['insurance']),
            'self_pay': round(b['self_pay']),
            'customers': _format_customers(bkey),
        })
    billing.sort(key=lambda b: -b['amount'])
    # Include all person_ids (individuals AND municipalities / other
    # facilities) so the user-list total matches the billing total. Drop
    # zero-net rows (pure journal noise). Each user carries its per-category
    # breakdown so the detail modal's chip filter can recompute totals on
    # the fly and hide users whose categories are all muted.
    users = []
    for aid, u in users_map.items():
        if round(u['amount']) == 0:
            continue
        cat_amounts = {
            c: {
                'amount': round(v['amount']),
                'insurance': round(v['insurance']),
                'self_pay': round(v['self_pay']),
            }
            for c, v in u['cat_amounts'].items()
            if round(v['amount']) != 0
        }
        users.append({
            'person_id': aid,
            'full_name': u['full_name'] or '',
            'amount': round(u['amount']),
            'insurance': round(u['insurance']),
            'self_pay': round(u['self_pay']),
            'cat_amounts': cat_amounts,
        })
    users.sort(key=lambda u: -u['amount'])

    return jsonify({
        'name': name,
        'total': round(total),
        'billing': billing,
        'users': users,
        'trend': trend,
    })


# ── Haifu (allocation) view ─────────────────────────────────────────────────
#
# Combines two data sources into a per-facility breakdown for the 配賦 tab:
#
#   1. Native revenue: sales_fmt_data aggregated by canonical card facility
#      name (_resolve_card_facility, same as /api/facility-cards) — what each
#      facility *itself* booked, identical to the card-view tab.
#   2. Allocation matrix: from the 売上配賦DB Google Sheet, fetched once
#      per snapshot run via haifu_sync.fetch() (best-effort, see snapshot.py).
#      Each Sheet row maps a (month, sales_code) to a positive distribution
#      across destination facilities and a negative source total at the
#      provider station.
#
# Output JSON shape per facility:
#   {
#     'name': '武拠点',                       # canonical display name
#     'native': [{category, service_name, amount}, ...],
#     'haifu_in':  [{counterpart, service, code, amount}, ...],   # +
#     'haifu_out': [{counterpart, service, code, amount}, ...],   # -
#     'native_total': int, 'haifu_in_total': int,
#     'haifu_out_total': int, 'total': int,
#   }


from config import HAIFU_DESTINATION_TO_OFFICE, HAIFU_SOURCE_TO_OFFICE


def _native_by_facility(conn, month):
    """sales_fmt_data aggregated by (canonical card facility name, category, service_name).

    Uses the same source / name resolution / service breakdown as
    /api/facility-cards so the haifu view's native section is identical to
    the card-view tab (no ¥差分, うらら splits unified, finer service grain)."""
    from collections import defaultdict
    rows = conn.execute('''
        SELECT base_name, credit_sub_account AS sub,
               COALESCE(SUM(credit_amount), 0) AS amt
        FROM sales_fmt_data
        WHERE service_month = ?
          AND base_name IS NOT NULL AND base_name != ''
        GROUP BY base_name, credit_sub_account
    ''', (month,)).fetchall()
    out = defaultdict(lambda: defaultdict(float))   # fac → (cat, svc) → amt
    for r in rows:
        fac_name = _resolve_card_facility(r['base_name'], r['sub'])
        if not fac_name:
            continue
        svc_name = _svc_label(r['sub'])
        cat = categorize_service(r['sub']) if r['sub'] else 'その他'
        key = (cat, svc_name)
        out[fac_name][key] += r['amt'] or 0
    return out


def build_haifu_for_month(conn, month, haifu_records):
    """Combine sales_fmt_data native rows + Sheet allocation records → list
    of facility cards for this month. `haifu_records` is the parsed full
    matrix from haifu_sync.parse(); it is filtered to this month here."""
    from collections import defaultdict

    native_map = _native_by_facility(conn, month)

    # facility name → {'native': [...], 'in': [...], 'out': [...]}
    facs = defaultdict(lambda: {'native': [], 'in': [], 'out': []})

    # Seed every facility that has native revenue this month.
    for fac, services in native_map.items():
        for (cat, svc), amt in services.items():
            facs[fac]['native'].append({
                'category': cat,
                'service_name': svc,
                'amount': round(amt),
            })

    # Apply allocation rows for this month.
    month_rows = [r for r in (haifu_records or []) if r.get('month') == month]
    for rec in month_rows:
        code = rec.get('code', '')
        svc_type = rec.get('service_type', '')

        # Resolve the source provider canonical name (negative side).
        source_canonical = None
        for src_label, _amt in (rec.get('sources') or {}).items():
            mapped = HAIFU_SOURCE_TO_OFFICE.get(src_label)
            if mapped:
                source_canonical = mapped
                break
        # If no source is mapped, the row's provider can't be resolved → skip.
        if not source_canonical:
            continue

        # Add 配賦 IN entries to each destination facility.
        for dest_label, amt in (rec.get('destinations') or {}).items():
            dest_canonical = HAIFU_DESTINATION_TO_OFFICE.get(dest_label)
            if not dest_canonical:
                continue
            if not amt:
                continue
            facs[dest_canonical]['in'].append({
                'counterpart': source_canonical,
                'service': svc_type,
                'code': code,
                'amount': round(amt),
            })
            # Mirror as 配賦 OUT on the source side (negative).
            facs[source_canonical]['out'].append({
                'counterpart': dest_canonical,
                'service': svc_type,
                'code': code,
                'amount': -round(amt),
            })

    out = []
    for name, parts in facs.items():
        # Sort: native by amount desc; haifu_in by amount desc; haifu_out
        # by amount asc (most-negative first).
        native = sorted(parts['native'], key=lambda r: -r['amount'])
        haifu_in = sorted(parts['in'], key=lambda r: -r['amount'])
        haifu_out = sorted(parts['out'], key=lambda r: r['amount'])
        nt = sum(r['amount'] for r in native)
        it = sum(r['amount'] for r in haifu_in)
        ot = sum(r['amount'] for r in haifu_out)
        if nt == 0 and it == 0 and ot == 0:
            continue
        out.append({
            'name': name,
            'native': native,
            'haifu_in': haifu_in,
            'haifu_out': haifu_out,
            'native_total': nt,
            'haifu_in_total': it,
            'haifu_out_total': ot,
            'total': nt + it + ot,
        })
    out.sort(key=lambda f: -f['total'])
    return out


@app.route('/api/haifu')
@login_required
def haifu():
    """Per-facility allocation view for the given month, sourced from
    sales_fmt_data + the 売上配賦DB Sheet (pre-loaded at snapshot time)."""
    month = request.args.get('month')
    if not month:
        return jsonify([])
    # haifu_records is loaded by snapshot.py before generate_api_json calls
    # the test client; we expose it on the Flask app config for thread-safe
    # passing without a global.
    haifu_records = app.config.get('HAIFU_RECORDS') or []
    conn = get_db()
    out = build_haifu_for_month(conn, month, haifu_records)
    conn.close()
    return jsonify(out)


# ── Management accounting journal (manual-entry xlsx) ───────────────────────
# Per-month per-facility aggregation of the 5 target sales codes from the
# manual-entry sheet (1650 / 2220 / 2240 / 2245 / 2299). Surfaces revenue
# items NOT captured by sales_summary / sales_fmt_data — used by the
# 拠点（配賦2）tab.

def build_mgmt_jrn_for_month(month, records):
    """Aggregate manage_jrn records by facility for the given month."""
    by_fac = {}
    for r in (records or []):
        if r.get('month') != month:
            continue
        fac = r.get('facility') or '(不明拠点)'
        rec = by_fac.setdefault(fac, {'name': fac, 'items': [], 'total': 0})
        rec['items'].append({
            'code': r.get('code'),
            'name': r.get('name'),
            'amount': r.get('amount') or 0,
        })
        rec['total'] += r.get('amount') or 0
    out = list(by_fac.values())
    for fac in out:
        fac['items'].sort(key=lambda x: -x['amount'])
    out.sort(key=lambda f: -f['total'])
    return out


@app.route('/api/mgmt-journal')
@login_required
def mgmt_journal():
    """Per-facility management-accounting (manual-entry) journal for the
    given month. Records are pre-loaded by snapshot.py before the test
    client is invoked, via app.config['MGMT_JRN_RECORDS']."""
    month = request.args.get('month')
    if not month:
        return jsonify([])
    records = app.config.get('MGMT_JRN_RECORDS') or []
    return jsonify(build_mgmt_jrn_for_month(month, records))


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
