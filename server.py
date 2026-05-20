"""
Sales Dashboard API (Demo) — reads pre-computed JSON from local data/ (or GCS fallback).

Run: python3 server.py

Auth: Google OAuth (any Google account).
  Requires env vars: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
  Optional: SECRET_KEY (defaults to random per-restart)
  Optional: DEEPSEEK_API_KEY, DEEPSEEK_MODEL (for AI analysis)
"""
import os
import functools
import secrets
import copy

from flask import Flask, jsonify, request, send_from_directory, redirect, session, url_for
from authlib.integrations.flask_client import OAuth

app = Flask(__name__, static_folder=os.path.dirname(__file__))
app.secret_key = (os.environ.get('SECRET_KEY') or secrets.token_hex(32)).strip()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.environ.get('GAE_ENV', '') == 'standard' or os.environ.get('K_SERVICE', ''),
)

# ── GCS data store ──────────────────────────────────────────────────────────
_store = None

def _get_store():
    global _store
    if _store is None:
        from gcs_store import GCSDataStore
        _store = GCSDataStore()
        data_dir = os.path.join(os.path.dirname(__file__), 'data')
        if os.path.isdir(data_dir):
            _store.load_from_local(data_dir)
        else:
            _store.load()
    return _store

# ── Google OAuth (any Google account) ──────────────────────────────────────────

oauth = OAuth(app)
oauth.register(
    name='google',
    client_id=(os.environ.get('GOOGLE_CLIENT_ID') or '').strip(),
    client_secret=(os.environ.get('GOOGLE_CLIENT_SECRET') or '').strip(),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'},
)


LOCAL_DEV = os.environ.get('LOCAL_DEV', '0') == '1'

def login_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get('user'):
            if LOCAL_DEV:
                session['user'] = {'email': 'dev@localhost', 'name': 'Dev User', 'picture': ''}
            elif request.path.startswith('/api/'):
                return jsonify({'error': 'unauthorized'}), 401
            else:
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
    # Accept any Google account (demo mode — no domain restriction)

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


# ── Static ────────────────────────────────────────────────────────────────────

@app.route('/')
@login_required
def index():
    resp = send_from_directory(os.path.dirname(__file__), 'index.html')
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp


@app.route('/about')
@login_required
def about():
    return redirect('/')

@app.route('/api/about-content')
@login_required
def about_content():
    import re
    path = os.path.join(os.path.dirname(__file__), 'about.html')
    with open(path, encoding='utf-8') as f:
        html = f.read()
    # Extract <style> + page content <div class="page">...</div>
    m_style = re.search(r'<style>(.*?)</style>', html, re.DOTALL)
    m_page = re.search(r'<div class="page">(.*?)</div>\s*<div class="footer">', html, re.DOTALL)
    style = m_style.group(1) if m_style else ''
    body = m_page.group(1) if m_page else ''
    style = style.replace('*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}', '')
    style = re.sub(r'body\{[^}]*\}', '', style)
    style = re.sub(r'[^}]*header[^{]*\{[^}]*\}', '', style)
    # Remove chat/FAB styles
    style = re.sub(r'#chat-fab\{[^}]*\}', '', style)
    style = re.sub(r'#chat-fab[^{]*\{[^}]*\}', '', style)
    style = re.sub(r'\.chat-panel\{[^}]*\}', '', style)
    style = re.sub(r'\.chat-panel[^{]*\{[^}]*\}', '', style)
    style = re.sub(r'\.chat-header[^{]*\{[^}]*\}', '', style)
    style = re.sub(r'\.chat-body[^{]*\{[^}]*\}', '', style)
    style = re.sub(r'\.chat-footer[^{]*\{[^}]*\}', '', style)
    style = re.sub(r'\.chat-thinking[^{]*\{[^}]*\}', '', style)
    style = re.sub(r'\.chat-suggestions[^{]*\{[^}]*\}', '', style)
    style = re.sub(r'\.dot-pulse[^{]*\{[^}]*\}', '', style)
    style = re.sub(r'@keyframes dot-bounce\{[^}]*\}', '', style)
    style = re.sub(r'\.page\{[^}]*\}', '', style)
    return '<style>' + style + '</style>' + body

@app.route('/sim')
@login_required
def sim():
    return send_from_directory(os.path.dirname(__file__), 'sim.html')


@app.route('/favicon.svg')
def favicon():
    return send_from_directory(os.path.dirname(__file__), 'favicon.svg', mimetype='image/svg+xml')


# ── Months + period helpers ──────────────────────────────────────────────────

@app.route('/api/months')
@login_required
def months():
    return jsonify(_get_store().get('months'))


def _period_months(start=None, end=None, month=None):
    """Resolve request params to a list of YYYY-MM strings.

    Accepts EITHER `month` (legacy single-month) OR `start`/`end` (range).
    If start==end (or only one is given), returns a 1-element list.
    Returns months known to the store, filtered to the requested range.
    """
    if start is None: start = request.args.get('start')
    if end   is None: end   = request.args.get('end')
    if month is None: month = request.args.get('month')
    if not (start or end):
        return [month] if month else []
    start = start or end
    end   = end   or start
    if start > end:
        start, end = end, start

    meta = _get_store().get('months') or []
    all_months = [m if isinstance(m, str) else m.get('month') for m in meta]
    # months.json is newest-first; sort chronologically so per_month[-1] = end month
    # and latest-month-wins dedup picks the correct (end-month) record for each person.
    return sorted(m for m in all_months if m and start <= m <= end)


def _sum_num(*vals):
    """Sum while tolerating None/missing."""
    return sum(v for v in vals if isinstance(v, (int, float)))


# ── KPI ───────────────────────────────────────────────────────────────────────

@app.route('/api/kpi')
@login_required
def kpi():
    s = _get_store()
    months_in_range = _period_months()
    if len(months_in_range) <= 1:
        m = months_in_range[0] if months_in_range else request.args.get('month')
        return jsonify(s.get(f'{m}/kpi'))

    # Range: sum financial fields. Counts are period-unique (set-union across
    # months) so the header reflects "how many distinct users / facilities
    # appeared anywhere in this period" rather than the end-month snapshot.
    per = [s.get(f'{m}/kpi') or {} for m in months_in_range]
    end = per[-1] if per else {}
    out = dict(end)
    sumf = lambda k: _sum_num(*(p.get(k) or 0 for p in per))
    for k in ('total', 'insurance', 'self_pay', 'new_customers', 'lost_customers'):
        out[k] = sumf(k)
    out['net_customers'] = (out.get('new_customers') or 0) - (out.get('lost_customers') or 0)
    user_ids = set()
    fac_names = set()
    for m in months_in_range:
        for p in (s.get(f'{m}/persons') or []):
            aid = p.get('person_id')
            if aid: user_ids.add(aid)
        for f in (s.get(f'{m}/facilities_billing') or []):
            nm = f.get('name')
            if nm: fac_names.add(nm)
    out['customers']  = len(user_ids) or end.get('customers')
    out['facilities'] = len(fac_names) or end.get('facilities')
    for k in ('change_total_pct', 'change_insurance_pct', 'change_self_pay_pct', 'change_customers_pct'):
        out[k] = None
    total = out.get('total') or 0
    cust  = out.get('customers') or 0
    facs  = out.get('facilities') or 0
    out['avg_per_customer']     = round(total / cust) if cust else 0
    out['avg_per_facility']     = round(total / facs) if facs else 0
    out['avg_ins_per_customer'] = round((out.get('insurance') or 0) / cust) if cust else 0
    out['avg_pay_per_customer'] = round((out.get('self_pay')  or 0) / cust) if cust else 0
    out['status_sentence'] = f"{months_in_range[0]}〜{months_in_range[-1]} 合計"
    return jsonify(out)


# ── Facilities ────────────────────────────────────────────────────────────────

@app.route('/api/facilities')
@login_required
def facilities():
    view  = request.args.get('view', 'billing')
    s = _get_store()
    months_in_range = _period_months()
    if len(months_in_range) <= 1:
        m = months_in_range[0] if months_in_range else request.args.get('month')
        return jsonify(copy.deepcopy(s.get(f'{m}/facilities_{view}') or []))

    # Merge by facility name. Sum numeric fields; union rev_by_cat.
    # trend (per-facility mini-sparkline) and categories taken from END month.
    per_month = [s.get(f'{m}/facilities_{view}') or [] for m in months_in_range]
    merged = {}
    order = []
    for rows in per_month:
        for r in rows:
            name = r.get('name')
            if not name:
                continue
            if name not in merged:
                merged[name] = copy.deepcopy(r)
                order.append(name)
                merged[name]['rev_by_cat'] = dict(r.get('rev_by_cat') or {})
            else:
                acc = merged[name]
                for k in ('total', 'insurance', 'self_pay'):
                    acc[k] = (acc.get(k) or 0) + (r.get(k) or 0)
                for cat, v in (r.get('rev_by_cat') or {}).items():
                    acc['rev_by_cat'][cat] = acc['rev_by_cat'].get(cat, 0) + v
    # Take trend/categories from the end-month row when present
    end_rows = per_month[-1]
    end_map = {r.get('name'): r for r in end_rows}
    for name, acc in merged.items():
        end_row = end_map.get(name)
        if end_row:
            if 'trend' in end_row: acc['trend'] = end_row['trend']
            if 'categories' in end_row: acc['categories'] = end_row['categories']
    return jsonify([merged[n] for n in order])


@app.route('/api/facility-detail')
@login_required
def facility_detail():
    """Drill-down for one facility. Response is a flat dict:
    {billing: [...], residents: [...], users: [...], billing_entities: [...]}.

    Range mode aggregates each array by its natural key (sales_type,
    (person_id, sales_type), entity, etc.) and sums the numeric fields
    so the drill-down totals match the range-aggregated main page.
    """
    name  = request.args.get('name')
    s = _get_store()
    months_in_range = _period_months()
    if len(months_in_range) <= 1:
        m = months_in_range[0] if months_in_range else request.args.get('month')
        return jsonify((s.get(f'{m}/facility_details') or {}).get(name) or {})

    per_month = [((s.get(f'{m}/facility_details') or {}).get(name) or {}) for m in months_in_range]

    def merge_list(rows_list, key_fn, sum_keys, keep_from_end=None):
        """Merge parallel lists by key_fn; sum numeric fields; preserve
        non-numeric identity fields (e.g. name) from the end-month entry."""
        acc = {}
        order = []
        for rows in rows_list:
            for r in rows or []:
                k = key_fn(r)
                if k is None: continue
                if k not in acc:
                    acc[k] = dict(r)
                    order.append(k)
                    # Reset numeric fields so we start clean and sum through the loop
                    for sk in sum_keys:
                        acc[k][sk] = 0
                for sk in sum_keys:
                    acc[k][sk] = (acc[k].get(sk) or 0) + (r.get(sk) or 0)
        # Overlay end-month identity fields (name/provider/etc.) when present
        if keep_from_end and rows_list and rows_list[-1]:
            end_map = {key_fn(r): r for r in rows_list[-1] if key_fn(r) is not None}
            for k, a in acc.items():
                er = end_map.get(k)
                if er:
                    for ek in keep_from_end:
                        if ek in er: a[ek] = er[ek]
        return [acc[k] for k in order]

    billing = merge_list(
        [p.get('billing') or [] for p in per_month],
        key_fn=lambda r: (r.get('service_name'), r.get('category')),
        sum_keys=('insurance', 'self_pay', 'total'),
    )
    users = merge_list(
        [p.get('users') or [] for p in per_month],
        key_fn=lambda r: (r.get('person_id'), r.get('provider_name')),
        sum_keys=('amount', 'insurance', 'self_pay'),
        keep_from_end=('full_name',),
    )
    residents = merge_list(
        [p.get('residents') or [] for p in per_month],
        key_fn=lambda r: (r.get('person_id'), r.get('sales_type'), r.get('billing_facility')),
        sum_keys=('amount', 'insurance', 'self_pay'),
        keep_from_end=('full_name', 'res_provider'),
    )
    billing_entities = merge_list(
        [p.get('billing_entities') or [] for p in per_month],
        key_fn=lambda r: r.get('entity'),
        sum_keys=('amount',),
    )
    return jsonify({
        'billing': sorted(billing, key=lambda r: -(r.get('total') or 0)),
        'users':   sorted(users,   key=lambda r: -(r.get('amount') or 0)),
        'residents': residents,
        'billing_entities': sorted(billing_entities, key=lambda r: -(r.get('amount') or 0)),
    })


# ── Services ──────────────────────────────────────────────────────────────────

@app.route('/api/services')
@login_required
def services():
    s = _get_store()
    months_in_range = _period_months()
    if len(months_in_range) <= 1:
        m = months_in_range[0] if months_in_range else request.args.get('month')
        return jsonify(copy.deepcopy(s.get(f'{m}/services') or []))

    # Merge by sales_type. Sum revenue; customers takes END month to avoid
    # double-counting users who appear in multiple months.
    per_month = [s.get(f'{m}/services') or [] for m in months_in_range]
    merged = {}
    order = []
    for rows in per_month:
        for r in rows:
            st = r.get('sales_type')
            if not st: continue
            if st not in merged:
                merged[st] = copy.deepcopy(r)
                order.append(st)
            else:
                acc = merged[st]
                for k in ('total', 'insurance', 'self_pay'):
                    acc[k] = (acc.get(k) or 0) + (r.get(k) or 0)
    end_map = {r.get('sales_type'): r for r in per_month[-1]}
    for st in merged:
        end_row = end_map.get(st)
        if end_row and 'customers' in end_row:
            merged[st]['customers'] = end_row['customers']
    return jsonify([merged[st] for st in order])


# ── Persons ───────────────────────────────────────────────────────────────────

@app.route('/api/service-users')
@login_required
def service_users():
    """Users who used a specific sales_type. Range mode aggregates across
    the period; a user with bills of the same sales_type in multiple months
    appears once with summed amounts."""
    sales_type = request.args.get('sales_type', '').strip()
    if not sales_type:
        return jsonify([])
    s = _get_store()
    months_in_range = _period_months() or [request.args.get('month')]

    acc = {}
    for m in months_in_range:
        all_details = s.get(f'{m}/person_details') or {}
        for aid, d in all_details.items():
            info = d.get('info') or {}
            hits = [b for b in (d.get('bills') or []) if b.get('sales_type') == sales_type]
            if not hits:
                continue
            amount    = sum(b.get('amount', 0) or 0 for b in hits)
            insurance = sum(b.get('insurance', 0) or 0 for b in hits)
            self_pay  = sum(b.get('self_pay', 0) or 0 for b in hits)
            facilities = {b.get('facility', '') for b in hits if b.get('facility')}
            if aid not in acc:
                acc[aid] = {
                    'person_id': aid, 'full_name': info.get('full_name'),
                    'amount': 0, 'insurance': 0, 'self_pay': 0, '_facs': set(),
                }
            acc[aid]['amount']    += amount
            acc[aid]['insurance'] += insurance
            acc[aid]['self_pay']  += self_pay
            acc[aid]['_facs']    |= facilities
            if info.get('full_name'): acc[aid]['full_name'] = info.get('full_name')
    result = []
    for aid, e in acc.items():
        result.append({
            'person_id': aid,
            'full_name': e['full_name'],
            'amount':    round(e['amount']),
            'insurance': round(e['insurance']),
            'self_pay':  round(e['self_pay']),
            'facilities': sorted(e['_facs']),
        })
    result.sort(key=lambda r: -r['amount'])
    return jsonify(result)


@app.route('/api/persons')
@login_required
def persons():
    q = request.args.get('q', '').strip()
    s = _get_store()
    months_in_range = _period_months()
    if len(months_in_range) <= 1:
        m = months_in_range[0] if months_in_range else request.args.get('month')
        data = s.get(f'{m}/persons') or []
    else:
        # Union by person_id; sum numeric fields; residence/full_name from END month.
        per_month = [s.get(f'{m}/persons') or [] for m in months_in_range]
        merged = {}
        order = []
        for rows in per_month:
            for p in rows:
                aid = p.get('person_id')
                if not aid: continue
                if aid not in merged:
                    merged[aid] = copy.deepcopy(p)
                    order.append(aid)
                else:
                    acc = merged[aid]
                    for k in ('total', 'insurance', 'self_pay', 'service_count'):
                        if k in p:
                            acc[k] = (acc.get(k) or 0) + (p.get(k) or 0)
        end_map = {p.get('person_id'): p for p in per_month[-1]}
        for aid, acc in merged.items():
            endp = end_map.get(aid)
            if endp:
                for k in ('full_name', 'residence'):
                    if k in endp: acc[k] = endp[k]
        data = [merged[aid] for aid in order]
    if q:
        ql = q.lower()
        data = [p for p in data if ql in (p.get('full_name', '') or '').lower()
                or str(p.get('person_id', '')) == q
                or ql in (p.get('residence', '') or '').lower()]
    return jsonify(data)


@app.route('/api/person-detail')
@login_required
def person_detail():
    """All bills for one person across all services and facilities.
    Range mode merges bills by (sales_type, facility) summing amounts;
    info / services / utilization taken from the end-month snapshot."""
    person_id = request.args.get('id')
    s = _get_store()
    months_in_range = _period_months()
    if len(months_in_range) <= 1:
        m = months_in_range[0] if months_in_range else request.args.get('month')
        return jsonify((s.get(f'{m}/person_details') or {}).get(str(person_id), {}))

    per_month = [((s.get(f'{m}/person_details') or {}).get(str(person_id)) or {}) for m in months_in_range]
    end = per_month[-1] if per_month else {}
    out = dict(end)

    # Merge bills across months by (sales_type, sub_sales_type, facility)
    bills_acc = {}
    order = []
    for p in per_month:
        for b in (p.get('bills') or []):
            k = (b.get('sales_type'), b.get('sub_sales_type'), b.get('facility'))
            if k not in bills_acc:
                bills_acc[k] = dict(b)
                order.append(k)
                for sk in ('amount', 'insurance', 'self_pay'):
                    bills_acc[k][sk] = 0
            for sk in ('amount', 'insurance', 'self_pay'):
                bills_acc[k][sk] = (bills_acc[k].get(sk) or 0) + (b.get(sk) or 0)
    out['bills'] = [bills_acc[k] for k in order]
    return jsonify(out)


@app.route('/api/analysis')
@login_required
def analysis():
    """Facility-centric analysis for executives.

    Range-mode aggregation rules:
      - Monetary (revenue, residential_potential, total_potential, unused_yen)
          → SUM across months. Each month of unused capacity is additive.
      - User counts (ins_count, self_pay_count, low_util_count,
        total_ins_persons, total_self_pay, total_low_util)
          → UNION by person_id where we have user-level data
            (limit_facilities + inst_facilities expose `ins_persons`
            and `self_pay_persons` lists with IDs). Otherwise fall back
            to end-month value.
      - avg_util → END-month snapshot (point-in-time).
      - care_level_distribution → END-month snapshot.
    """
    s = _get_store()
    months_in_range = _period_months()
    if len(months_in_range) <= 1:
        m = months_in_range[0] if months_in_range else request.args.get('month')
        return jsonify(s.get(f'{m}/analysis'))

    per_month = [s.get(f'{m}/analysis') or {} for m in months_in_range]
    out = copy.deepcopy(per_month[-1])

    # Helper: union person_ids across month lists, accepting dicts or bare ids.
    def _union_ids(per_month_lists):
        ids = set()
        for lst in per_month_lists:
            for p in (lst or []):
                aid = p.get('person_id') if isinstance(p, dict) else p
                if aid is not None:
                    ids.add(str(aid))
        return ids

    # ── Monetary top-level KPIs ──────────────────────────────────────────
    for k in ('residential_potential', 'total_potential'):
        out[k] = sum((mo.get(k) or 0) for mo in per_month)

    # ── Period classification: "ever-insurance" semantics ───────────────────
    # Single-month: each resident is classified as insurance-user (care_ins_yen>0)
    # or 自費のみ (care_ins_yen=0) for that month. Period extension: a resident
    # counts as 保険利用 if they used insurance in AT LEAST ONE month of the
    # period; they count as 自費のみ only if they never used insurance across
    # the entire period. 保険利用 + 自費のみ = unique residents in period.
    #
    # care_level_distribution: for 保険利用 residents, use their latest-month
    # inferred_level (most recent care level observed); for 自費のみ residents,
    # all fall under the '自費のみ' bucket.
    persons = {}  # person_id → {'ever_ins': bool, 'latest_ins_record': dict|None, 'latest_record': dict}
    for mo in per_month:
        for fac in (mo.get('facilities') or []) + (mo.get('inst_facilities') or []):
            for p in (fac.get('ins_persons') or []):
                aid = p.get('person_id')
                if aid is None: continue
                rec = persons.setdefault(aid, {'ever_ins': False, 'latest_ins_record': None, 'latest_record': None})
                rec['ever_ins'] = True
                rec['latest_ins_record'] = p
                rec['latest_record'] = p
            for p in (fac.get('self_pay_persons') or []):
                aid = p.get('person_id')
                if aid is None: continue
                rec = persons.setdefault(aid, {'ever_ins': False, 'latest_ins_record': None, 'latest_record': None})
                rec['latest_record'] = p   # don't flip ever_ins

    if persons:
        ins_recs = [r for r in persons.values() if r['ever_ins']]
        sp_recs  = [r for r in persons.values() if not r['ever_ins']]
        out['total_ins_persons'] = len(ins_recs)
        out['total_self_pay']    = len(sp_recs)
        out['total_low_util']    = sum(1 for r in ins_recs
                                       if (r['latest_ins_record'].get('util_pct') or 0) < 50)

        # care_level_distribution: ever-ins → latest ins record's inferred_level;
        # never-ins → '自費のみ'. clTotal = total_ins + total_self_pay by construction.
        dist = {}
        for r in ins_recs:
            lvl = (r['latest_ins_record'] or {}).get('inferred_level')
            if lvl and lvl != '自費のみ':
                dist[lvl] = dist.get(lvl, 0) + 1
        dist['自費のみ'] = len(sp_recs)
        end_dist = per_month[-1].get('care_level_distribution') or {}
        merged_dist = {k: 0 for k in end_dist}
        merged_dist.update(dist)
        out['care_level_distribution'] = merged_dist
    else:
        # No person lists — fall back to end-month scalars
        for k in ('total_ins_persons', 'total_self_pay', 'total_low_util'):
            out[k] = per_month[-1].get(k) or 0

    # Other counts: use end-month (point-in-time scalars)
    for k in ('total_ins_all', 'total_pipeline', 'avg_tenure'):
        out[k] = per_month[-1].get(k) or 0

    # ── Merge per-facility lists ─────────────────────────────────────────
    def merge_fac_list(list_key, has_user_ids=False):
        """Merge per-facility rows across months. Monetary fields sum; user
        counts come from unioning ins_persons/self_pay_persons when available
        (residential), else fall back to end-month values."""
        order = []
        merged = {}
        monetary_keys = ('revenue', 'total_potential')
        for mo in per_month:
            for f in (mo.get(list_key) or []):
                name = f.get('office_name') or f.get('name')
                if not name: continue
                if name not in merged:
                    merged[name] = copy.deepcopy(f)
                    order.append(name)
                    if has_user_ids:
                        merged[name]['ins_persons']       = list(f.get('ins_persons') or [])
                        merged[name]['self_pay_persons']  = list(f.get('self_pay_persons') or [])
                else:
                    acc = merged[name]
                    for k in monetary_keys:
                        if k in f:
                            acc[k] = (acc.get(k) or 0) + (f.get(k) or 0)
                    if has_user_ids:
                        acc['ins_persons']      = (acc.get('ins_persons') or []) + (f.get('ins_persons') or [])
                        acc['self_pay_persons'] = (acc.get('self_pay_persons') or []) + (f.get('self_pay_persons') or [])

        # End-month snapshot fields take their final value (point-in-time data)
        end_map = {(f.get('office_name') or f.get('name')): f for f in (per_month[-1].get(list_key) or [])}

        for name, f in merged.items():
            end_f = end_map.get(name) or {}
            # avg_util → end-month value (point-in-time; summing/averaging across
            # months without weighting would be misleading).
            f['avg_util'] = end_f.get('avg_util', f.get('avg_util', 0))
            if has_user_ids:
                # De-duplicate ins_persons / self_pay_persons by person_id,
                # keeping the latest-month entry (larger unused_yen or newest).
                def dedup(plist):
                    by_id = {}
                    for p in plist:
                        aid = p.get('person_id')
                        if aid is None: continue
                        by_id[aid] = p   # later months overwrite; end-month wins
                    return list(by_id.values())
                ins_de = dedup(f.get('ins_persons') or [])
                sp_de  = dedup(f.get('self_pay_persons') or [])
                f['ins_persons']      = ins_de
                f['self_pay_persons'] = sp_de
                if ins_de or sp_de:
                    # Person lists present → exact union
                    f['ins_count']      = len(ins_de)
                    f['self_pay_count'] = len(sp_de)
                    f['low_util_count'] = sum(1 for p in ins_de if (p.get('util_pct') or 0) < 50)
                    f['active'] = len({p.get('person_id') for p in ins_de} |
                                      {p.get('person_id') for p in sp_de})
                else:
                    # inst_facilities don't populate person lists — fall back to end-month
                    # counts (same as the svc_facilities branch). active is the union of
                    # resident counts — best available without person_id data.
                    for k in ('active', 'ins_count', 'self_pay_count', 'low_util_count'):
                        f[k] = end_f.get(k, f.get(k, 0))
            else:
                # No user-level data → take end-month counts
                for k in ('active', 'ins_count', 'self_pay_count', 'low_util_count'):
                    f[k] = end_f.get(k, f.get(k, 0))
            # avg_revenue recomputed from summed revenue / active
            if f.get('active'):
                f['avg_revenue'] = round((f.get('revenue') or 0) / f['active'])

        return [merged[n] for n in order] if order else (out.get(list_key) or [])

    out['facilities']      = merge_fac_list('facilities',      has_user_ids=True)
    out['inst_facilities'] = merge_fac_list('inst_facilities', has_user_ids=True)
    out['svc_facilities']  = merge_fac_list('svc_facilities',  has_user_ids=False)

    return jsonify(out)


@app.route('/api/cross-sell')
@login_required
def cross_sell():
    """Cross-sell opportunities for residential customers.
    Range mode returns the END-month snapshot (point-in-time, per-user)."""
    months_in_range = _period_months()
    m = months_in_range[-1] if months_in_range else request.args.get('month')
    return jsonify(_get_store().get(f'{m}/cross_sell'))


@app.route('/api/trend-history')
@login_required
def trend_history():
    """Monthly total billing per service category + facility.

    Default (no params): returns the full ~16 month history baked at snapshot.
    With start/end: filters rows to that period so the 推移 tab respects the
    period selector at the header.
    """
    th = _get_store().get('trend_history') or {}
    months = _period_months()
    if not months:
        return jsonify(th)
    keep = set(months)
    return jsonify({
        'categories': [r for r in (th.get('categories') or []) if r.get('service_month') in keep],
        'facilities': [r for r in (th.get('facilities') or []) if r.get('service_month') in keep],
    })


def _map_category(r):
    cats = sorted((r.get('rev_by_cat') or {}).keys())
    return '+'.join(cats) if cats else 'その他'

@app.route('/api/maps-key')
@login_required
def maps_key():
    return jsonify({'key': os.environ.get('GOOGLE_MAPS_API_KEY', '')})

@app.route('/api/map')
@login_required
def map_data():
    """Facility locations with revenue data for map display."""
    s = _get_store()
    registry = s.get('facility_registry') or {}
    by_name = {r.get('office_name'): r for r in registry}
    months_in_range = _period_months()
    if len(months_in_range) <= 1:
        m = months_in_range[0] if months_in_range else request.args.get('month')
        rows = s.get(f'{m}/map') or []
        return jsonify([{**by_name.get(r['office_name'], {}), **r, 'category': _map_category(r)} for r in rows if r.get('office_name') in by_name])

    per_month = [s.get(f'{m}/map') or [] for m in months_in_range]
    merged = {}
    order = []
    for rows in per_month:
        for r in rows:
            name = r.get('office_name')
            if not name or name not in by_name: continue
            if name not in merged:
                merged[name] = dict(r)
                order.append(name)
                merged[name]['rev_by_cat'] = dict(r.get('rev_by_cat') or {})
            else:
                acc = merged[name]
                acc['revenue'] = (acc.get('revenue') or 0) + (r.get('revenue') or 0)
                for cat, v in (r.get('rev_by_cat') or {}).items():
                    acc['rev_by_cat'][cat] = acc['rev_by_cat'].get(cat, 0) + v
    end_map = {r.get('office_name'): r for r in per_month[-1]}
    for name, acc in merged.items():
        end_row = end_map.get(name)
        if end_row:
            acc['active'] = end_row.get('active', acc.get('active', 0))
        active = acc.get('active') or 0
        revenue = acc.get('revenue') or 0
        acc['avg_revenue'] = round(revenue / active) if active and revenue else 0
    return jsonify([{**by_name.get(n, {}), **merged[n], 'category': _map_category(merged[n])} for n in order])


@app.route('/api/facility-trend')
@login_required
def facility_trend():
    """Per-category revenue over time for one facility.

    With no range params: last 6 months (legacy behaviour).
    With start/end:       filters to the requested range, stitching across
                          each monthly facility_trends.json when the range
                          extends beyond the latest 6-month window.
    """
    name = request.args.get('name')
    s = _get_store()
    meta = s.get('months') or []
    all_months = sorted(m if isinstance(m, str) else m.get('month') for m in meta)

    start = request.args.get('start')
    end   = request.args.get('end')
    if not (start or end):
        latest = all_months[-1] if all_months else ''
        all_trends = s.get(f'{latest}/facility_trends') or {}
        return jsonify(all_trends.get(name, []))

    start = start or end
    end   = end   or start
    # Stitch: pick the facility_trends.json that would contain each month;
    # use the END month's snapshot as the source of truth for months it
    # contains, fall back to older snapshots for older months outside the
    # 6-month window.
    seen = {}   # service_month → row
    for m in reversed(all_months):
        trends = (s.get(f'{m}/facility_trends') or {}).get(name) or []
        for row in trends:
            sm = row.get('service_month')
            if sm and sm not in seen and start <= sm <= end:
                seen[sm] = row
        if start >= m:
            break
    return jsonify([seen[k] for k in sorted(seen.keys())])


@app.route('/api/alerts')
@login_required
def alerts():
    """MoM changes: revenue swings, new/lost users.

    Single-month (or no range): returns that month's alerts object directly
    ({summary, details}). Range mode returns the end-month alerts at the top
    level (for backward-compat pills) plus a `months` array with every month's
    {month, summary, details} so the UI can render a per-month breakdown."""
    s = _get_store()
    months_in_range = _period_months()
    if not months_in_range:
        m = request.args.get('month')
        return jsonify(s.get(f'{m}/alerts'))
    end = months_in_range[-1]
    end_data = s.get(f'{end}/alerts') or {}
    if len(months_in_range) <= 1:
        return jsonify(end_data)
    per_month = []
    for m in months_in_range:
        d = s.get(f'{m}/alerts') or {}
        per_month.append({
            'month': m,
            'summary': d.get('summary') or {},
            'details': d.get('details') or [],
        })
    return jsonify({**end_data, 'months': per_month})


# ── Manual snapshot trigger ──────────────────────────────────────────────────

_admin_env = os.environ.get('ADMIN_EMAILS', '')
ADMIN_EMAILS = set(e.strip() for e in _admin_env.split(',') if e.strip()) if _admin_env else set()
SNAPSHOT_COOLDOWN = 1800  # 30 minutes

@app.route('/api/trigger-snapshot', methods=['POST'])
@login_required
def trigger_snapshot():
    """Trigger the monthly snapshot job on demand. Admin-only, with cooldown."""
    user = session.get('user', {})
    if user.get('email') not in ADMIN_EMAILS:
        return jsonify({'error': 'Admin access required'}), 403

    # Layer 2: check GCS timestamp as debounce
    try:
        from google.cloud import storage
        client = storage.Client()
        bucket_name = os.environ.get('GCS_BUCKET', 'demo-dashboard-data')
        bucket = client.bucket(bucket_name)
        blob = bucket.get_blob('months.json')
        if blob and blob.updated:
            import datetime
            age = (datetime.datetime.now(datetime.timezone.utc) - blob.updated).total_seconds()
            if age < SNAPSHOT_COOLDOWN:
                remaining = int((SNAPSHOT_COOLDOWN - age) / 60)
                return jsonify({'error': f'Update ran {int(age/60)} min ago. Wait ~{remaining} min.'}), 429
    except Exception:
        pass  # if GCS check fails, allow the trigger

    # Trigger Cloud Run Job (configurable via env vars)
    try:
        import google.auth
        import google.auth.transport.requests
        import requests as http_requests
        credentials, project = google.auth.default()
        credentials.refresh(google.auth.transport.requests.Request())
        job_url = os.environ.get('SNAPSHOT_JOB_URL', '')
        if not job_url:
            return jsonify({'error': 'SNAPSHOT_JOB_URL not configured'}), 500
        resp = http_requests.post(
            job_url,
            headers={'Authorization': f'Bearer {credentials.token}'},
        )
        if resp.status_code in (200, 201):
            return jsonify({'status': 'triggered', 'message': 'Snapshot job started. Data will refresh in ~2 minutes.'})
        return jsonify({'error': f'GCP returned {resp.status_code}'}), 502
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Card View (journal_entries source) ────────────────────────────────────────
#
# Runtime endpoints that read the pre-computed facility_cards.json and
# facility_cards_detail.json from the bucket. Single-month passthrough;
# multi-month merges by (facility, (category, service_name)) summing amounts
# and splits.

@app.route('/api/facility-cards')
@login_required
def facility_cards():
    s = _get_store()
    months_in_range = _period_months()
    if len(months_in_range) <= 1:
        m = months_in_range[0] if months_in_range else request.args.get('month')
        return jsonify(s.get(f'{m}/facility_cards') or [])
    by_name = {}
    for m in months_in_range:
        for fac in (s.get(f'{m}/facility_cards') or []):
            rec = by_name.setdefault(fac['name'], {
                'name': fac['name'], 'total': 0, 'categories': set(), 'services_map': {}
            })
            rec['total'] += fac.get('total', 0)
            for c in (fac.get('categories') or []):
                rec['categories'].add(c)
            for svc in (fac.get('services') or []):
                key = (svc.get('category'), svc.get('service_name'))
                s_rec = rec['services_map'].setdefault(key, {
                    'category': svc.get('category'),
                    'service_name': svc.get('service_name'),
                    'code': svc.get('code'),
                    'amount': 0,
                })
                s_rec['amount'] += svc.get('amount', 0)
    out = []
    for rec in by_name.values():
        out.append({
            'name': rec['name'],
            'total': rec['total'],
            'categories': sorted(rec['categories']),
            'services': sorted(rec['services_map'].values(), key=lambda s: -s['amount']),
        })
    out.sort(key=lambda f: -f['total'])
    return jsonify(out)


@app.route('/api/facility-cards-detail')
@login_required
def facility_cards_detail():
    s = _get_store()
    name = request.args.get('name')
    if not name:
        return jsonify({'name': '', 'total': 0, 'billing': [], 'users': [], 'trend': []})
    months_in_range = _period_months()
    if len(months_in_range) <= 1:
        m = months_in_range[0] if months_in_range else request.args.get('month')
        detail = (s.get(f'{m}/facility_cards_detail') or {}).get(name)
        if detail:
            return jsonify(detail)
        return jsonify({'name': name, 'total': 0, 'billing': [], 'users': [], 'trend': []})
    # Range: merge billing by (category, service_name), users by person_id,
    # trend = union of monthly totals for the facility across the range.
    billing_map = {}
    users_map = {}
    trend_map = {}
    total = 0
    for m in months_in_range:
        bundle = (s.get(f'{m}/facility_cards_detail') or {}).get(name)
        if not bundle:
            continue
        total += bundle.get('total') or 0
        for b in (bundle.get('billing') or []):
            key = (b.get('category'), b.get('service_name'))
            rec = billing_map.setdefault(key, {
                'category': b.get('category'),
                'service_name': b.get('service_name'),
                'code': b.get('code'),
                'amount': 0, 'insurance': 0, 'self_pay': 0,
                'customers_map': {},
            })
            rec['amount'] += b.get('amount') or 0
            rec['insurance'] += b.get('insurance') or 0
            rec['self_pay'] += b.get('self_pay') or 0
            for c in (b.get('customers') or []):
                is_fac = bool(c.get('is_facility'))
                ckey = c.get('name') or '' if is_fac else '__residents__'
                cm = rec['customers_map'].setdefault(ckey, {
                    'name': c.get('name'),
                    'amount': 0, 'insurance': 0, 'self_pay': 0,
                    'is_facility': is_fac,
                    'aids_set': set(),
                })
                cm['amount'] += c.get('amount') or 0
                cm['insurance'] += c.get('insurance') or 0
                cm['self_pay'] += c.get('self_pay') or 0
                # Union aids across months → accurate unique-count in range
                for a in (c.get('aids') or []):
                    cm['aids_set'].add(a)
        for u in (bundle.get('users') or []):
            aid = u.get('person_id')
            if not aid:
                continue
            rec = users_map.setdefault(aid, {
                'person_id': aid,
                'full_name': u.get('full_name') or '',
                'amount': 0, 'insurance': 0, 'self_pay': 0,
                'cat_amounts': {},
            })
            rec['amount'] += u.get('amount') or 0
            rec['insurance'] += u.get('insurance') or 0
            rec['self_pay'] += u.get('self_pay') or 0
            for cat, ca in (u.get('cat_amounts') or {}).items():
                dst = rec['cat_amounts'].setdefault(
                    cat, {'amount': 0, 'insurance': 0, 'self_pay': 0}
                )
                dst['amount'] += ca.get('amount') or 0
                dst['insurance'] += ca.get('insurance') or 0
                dst['self_pay'] += ca.get('self_pay') or 0
            if not rec['full_name']:
                rec['full_name'] = u.get('full_name') or ''
        # Trend merge: each detail bundle's trend is that month's context, may
        # include up to 12 prior months. Use the last-writer-wins (later m
        # overrides earlier) to end with the most recent month's view.
        for t in (bundle.get('trend') or []):
            tm = t.get('month')
            if tm:
                trend_map[tm] = t.get('total') or 0
    # Drop zero-net billing rows (offsetting journal entries). Flatten
    # customers_map → sorted customers list. Pool entries get their name
    # regenerated from the unioned aid count so "個人 N名" reflects the
    # true distinct residents across the selected period.
    billing = []
    for b in billing_map.values():
        if round(b['amount']) == 0:
            continue
        customers = []
        for c in b['customers_map'].values():
            if round(c['amount']) == 0:
                continue
            count = len(c['aids_set'])
            name = c['name']
            if not c['is_facility']:
                name = f'個人 {count}名' if count else '個人'
            customers.append({
                'name': name,
                'amount': c['amount'],
                'insurance': c['insurance'],
                'self_pay': c['self_pay'],
                'is_facility': c['is_facility'],
                'count': count,
            })
        customers.sort(key=lambda c: -c['amount'])
        billing.append({
            'category': b['category'],
            'service_name': b['service_name'],
            'code': b['code'],
            'amount': b['amount'],
            'insurance': b['insurance'],
            'self_pay': b['self_pay'],
            'customers': customers,
        })
    billing.sort(key=lambda b: -b['amount'])
    # Drop zero-net users (nothing meaningful to show).
    users = sorted(
        (u for u in users_map.values() if round(u['amount']) != 0),
        key=lambda u: -u['amount'],
    )
    # In range mode, clip the trend to the selected period so the chart
    # matches what the user picked rather than defaulting to the snapshot's
    # 12-month context window.
    period_set = set(months_in_range)
    trend = [
        {'month': k, 'total': v}
        for k, v in sorted(trend_map.items())
        if k in period_set
    ]
    return jsonify({
        'name': name, 'total': total,
        'billing': billing, 'users': users, 'trend': trend,
    })


@app.route('/api/haifu')
@login_required
def haifu():
    """Allocation view: per-facility breakdown of native + 社内間配賦 IN/OUT.

    Single-month: passthrough of {month}/haifu.json (pre-built by snapshot).
    Range mode: merge facilities by name; sum native by (category, service),
    sum haifu_in/out by (counterpart, code). Order is stable: largest total
    first; within IN, biggest amount first; within OUT, most-negative first.
    """
    s = _get_store()
    months_in_range = _period_months()
    if len(months_in_range) <= 1:
        m = months_in_range[0] if months_in_range else request.args.get('month')
        return jsonify(s.get(f'{m}/haifu') or [])
    by_name = {}
    for m in months_in_range:
        for fac in (s.get(f'{m}/haifu') or []):
            rec = by_name.setdefault(fac['name'], {
                'name': fac['name'],
                'native_map': {}, 'in_map': {}, 'out_map': {},
            })
            for n in (fac.get('native') or []):
                key = (n.get('category'), n.get('service_name'))
                slot = rec['native_map'].setdefault(key, {**n, 'amount': 0})
                slot['amount'] += n.get('amount') or 0
            for h in (fac.get('haifu_in') or []):
                key = (h.get('counterpart'), h.get('code'))
                slot = rec['in_map'].setdefault(key, {**h, 'amount': 0})
                slot['amount'] += h.get('amount') or 0
            for h in (fac.get('haifu_out') or []):
                key = (h.get('counterpart'), h.get('code'))
                slot = rec['out_map'].setdefault(key, {**h, 'amount': 0})
                slot['amount'] += h.get('amount') or 0
    out = []
    for rec in by_name.values():
        native = sorted(rec['native_map'].values(), key=lambda r: -r['amount'])
        haifu_in = sorted(rec['in_map'].values(), key=lambda r: -r['amount'])
        haifu_out = sorted(rec['out_map'].values(), key=lambda r: r['amount'])
        nt = sum(r['amount'] for r in native)
        it = sum(r['amount'] for r in haifu_in)
        ot = sum(r['amount'] for r in haifu_out)
        if nt == 0 and it == 0 and ot == 0:
            continue
        out.append({
            'name': rec['name'],
            'native': native, 'haifu_in': haifu_in, 'haifu_out': haifu_out,
            'native_total': nt, 'haifu_in_total': it, 'haifu_out_total': ot,
            'total': nt + it + ot,
        })
    out.sort(key=lambda f: -f['total'])
    return jsonify(out)


@app.route('/api/mgmt-journal')
@login_required
def mgmt_journal():
    """Management-accounting (manual-entry xlsx) per-facility journal.

    Single-month: passthrough of {month}/mgmt_journal.json (pre-built by
    snapshot). Range mode: merge facilities by name; sum item amounts by
    (code, name)."""
    s = _get_store()
    months = _period_months()
    if len(months) <= 1:
        m = months[0] if months else request.args.get('month')
        return jsonify(s.get(f'{m}/mgmt_journal') or [])
    by_name = {}
    for m in months:
        for fac in (s.get(f'{m}/mgmt_journal') or []):
            rec = by_name.setdefault(fac['name'], {
                'name': fac['name'], 'items_map': {}, 'total': 0,
            })
            for it in (fac.get('items') or []):
                key = (it.get('code'), it.get('name'))
                slot = rec['items_map'].setdefault(key, {**it, 'amount': 0})
                slot['amount'] += it.get('amount') or 0
            rec['total'] += fac.get('total') or 0
    out = []
    for rec in by_name.values():
        items = sorted(rec['items_map'].values(), key=lambda x: -x['amount'])
        out.append({'name': rec['name'], 'items': items, 'total': rec['total']})
    out.sort(key=lambda f: -f['total'])
    return jsonify(out)


# ── AI Analysis (OpenAI) ─────────────────────────────────────────────────────
# scope-restricted, period-aware, filter-aware on-demand AI commentary.
# Lazy-imported so a missing/old `openai` package doesn't crash startup.
import time
import hashlib
import json as _json

from config import categorize_service

_AI_CACHE = {}              # cache_key -> (text, expiry_ts, meta_str)
_AI_RATE = {}               # user_email -> [timestamp,...]
_AI_GLOBAL_RATE = []        # all AI call timestamps (prevents Sybil account rotation)
_AI_TTL = 300               # 5 min
_AI_RATE_WINDOW = 3600      # 1h sliding window
_AI_RATE_MAX = 30           # max calls / window / user
_AI_GLOBAL_MAX = 200        # max total AI calls / hour (caps DeepSeek spend)

# Field keep-sets for sanitization. Anything not listed is dropped before the
# payload is sent to OpenAI — saves tokens and avoids exposing internal IDs.
_FAC_CARD_KEEP   = {'name', 'total', 'categories', 'services'}
_SVC_KEEP        = {'category', 'service_name', 'amount'}
_HAIFU_FAC_KEEP  = {'name', 'native_total', 'haifu_in_total', 'haifu_out_total',
                    'total', 'haifu_in', 'haifu_out'}
_HAIFU_ROW_KEEP  = {'counterpart', 'service', 'amount'}
_BILLING_KEEP    = {'category', 'service_name', 'amount', 'insurance', 'self_pay'}
_CUSTOMER_KEEP   = {'name', 'amount', 'is_facility'}
_USER_KEEP       = {'full_name', 'cat_amounts'}
_PERSON_INFO_KEEP = {'full_name', 'birthday', 'gender', 'customer_type', 'flag'}
_BILL_KEEP       = {'amount', 'facility', 'insurance', 'sales_type', 'self_pay', 'sub_sales_type'}
_PERSON_SVC_KEEP = {'contract_start', 'contract_end', 'office_name', 'provider_name', 'status'}


def _pick(d, keys):
    """Return a NEW dict containing only the wanted keys. Never mutates input."""
    return {k: v for k, v in (d or {}).items() if k in keys}


# English JSON keys → Japanese. Applied as the final step before sending to
# OpenAI so the model never sees English identifiers in the data — which
# previously caused leaked terms like "native", "haifu_in" in the output.
_KEY_TO_JA = {
    # facility / card view
    'name': '名前', 'total': '合計', 'categories': 'カテゴリ', 'services': 'サービス内訳',
    'category': 'カテゴリ', 'service_name': 'サービス名', 'amount': '金額',
    # haifu
    'native_total': '自拠点売上計', 'haifu_in_total': '配賦流入計',
    'haifu_out_total': '配賦流出計',
    'haifu_in': '配賦流入', 'haifu_out': '配賦流出',
    'counterpart': '相手先', 'service': 'サービス種別',
    # billing / users
    'billing': '請求内訳', 'customers': '請求元', 'is_facility': '拠点請求',
    'users': '利用者', 'full_name': '氏名', 'cat_amounts': 'カテゴリ別金額',
    'insurance': '保険負担', 'self_pay': '自己負担',
    # person
    'info': '基本情報', 'birthday': '生年月日', 'gender': '性別',
    'customer_type': '顧客区分', 'flag': 'フラグ',
    'bills': '月次請求', 'facility': '請求拠点',
    'sales_type': 'サービス', 'sub_sales_type': 'サブサービス',
    'contract_start': '契約開始', 'contract_end': '契約終了',
    'office_name': '居住拠点', 'provider_name': 'サービス提供', 'status': '利用状況',
    # trend / containers
    'trend': '月次推移', 'month': '月',
    'facility_cards': '拠点別カード一覧', 'haifu': '社内間配賦一覧',
    'mgmt_journal': '管理会計収入一覧', 'mgmt_per_month': '管理会計収入_月別',
    'card_per_month': '拠点詳細_月別', 'haifu_per_month': '社内間配賦_月別',
    'person_per_month': '利用者_月別',
    # mgmt journal items
    'items': '項目', 'code': '科目CD',
    # facilities_billing
    'facilities_billing': '拠点別請求一覧', 'rev_by_cat': 'カテゴリ別売上',
    'active': '稼働利用者数',
    # analysis
    'analysis': '分析サマリ', 'cross_sell': 'クロスセル候補',
    'targets_top50': '営業候補上位50', 'service_density': 'サービス利用数分布',
    'facilities': '居宅系拠点',
    'inst_facilities': '施設系拠点', 'svc_facilities': 'サービス拠点',
    'care_level_distribution': '介護度分布(推定)',
    'residential_potential': '居宅系保険未使用枠',
    'ins_persons_top5': '保険利用者上位5名',
    'inferred_level': '推定介護度', 'monthly_total': '月額合計',
    'limit_yen': '月額上限', 'unused_yen': '未使用枠', 'util_pct': '利用率',
    'care_ins_yen': '保険請求額', 'unit_price': '単価', 'exceeded_yen': '超過額',
    'office_name': '拠点名', 'region': '地域', 'revenue': '売上',
    'avg_revenue': '平均売上', 'avg_util': '平均利用率',
    'ins_count': '保険利用人数', 'low_util_count': '低利用率人数',
    'total_potential': '保険未使用枠合計',
    'total_ins_all': '保険売上総額', 'total_ins_persons': '保険利用人数',
    'total_low_util': '低利用率人数', 'total_pipeline': '検討中(無効)',
    'avg_tenure': '平均在籍月数', 'has_limit_analysis': '限度額分析対象',
    # persons
    'persons': '利用者一覧', 'period_summary': '期間サマリ',
    'top_persons': '上位利用者', 'service_count_distribution': 'サービス利用数分布',
    'total_persons': '総利用者数', 'total_amount': '総売上',
    'total_insurance': '保険負担合計', 'total_self_pay': '自己負担合計',
    'avg_amount': '平均売上',
    'residence': '居住拠点', 'service_count': 'サービス利用数',
    # global
    'monthly_kpi': '月次KPI', 'monthly_alerts': '月次アラート',
    # trend history
    'trend_history': '推移履歴', 'facilities_top30': '拠点別推移上位30',
    'points': '月次値',
    # facility detail (拠点詳細)
    'residents_top30': '居住者上位30', 'users_top30': '利用者上位30',
    'billing_entities': '請求事業所', 'billing_facility': '請求拠点',
    # care-level / svc-users / fac-gap modals
    'care_level': '介護度', 'persons_per_month': '利用者_月別',
    'gap_per_month': 'ギャップ_月別',
    'users_per_month': '利用者_月別', 'users_top30': '利用者上位30',
    'total_users': '総利用者数',
    '_group': 'グループ', '_facility': '拠点',
    # facilities-compare
    'mode': 'モード', 'series': '拠点別系列',
    'value': '値', 'value_rel_pct': '相対値(%)', 'residents': '居住者数',
    'per_user': '一人当たり', 'y_axis': 'Y軸',
    'selected': '選択拠点',
    # services / persons / history filter context
    '_sort': '並び替え', '_view_mode': '表示モード',
}


def _translate_keys_to_ja(obj):
    """Recursively rename dict keys per _KEY_TO_JA. Date / numeric keys
    (e.g. '2026-03', amounts) pass through unchanged."""
    if isinstance(obj, dict):
        return {_KEY_TO_JA.get(k, k): _translate_keys_to_ja(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_translate_keys_to_ja(x) for x in obj]
    return obj


def _sanitize_card(c):
    if not c:
        return None
    out = _pick(c, _FAC_CARD_KEEP)
    out['services'] = [_pick(s, _SVC_KEEP) for s in (c.get('services') or [])]
    return out


def _sanitize_haifu_fac(f):
    if not f:
        return None
    out = _pick(f, _HAIFU_FAC_KEEP)
    out['haifu_in']  = [_pick(r, _HAIFU_ROW_KEEP) for r in (f.get('haifu_in')  or [])]
    out['haifu_out'] = [_pick(r, _HAIFU_ROW_KEEP) for r in (f.get('haifu_out') or [])]
    return out


def _sanitize_card_detail(d):
    if not d:
        return None
    return {
        'name':    d.get('name'),
        'total':   d.get('total'),
        'billing': [
            {**_pick(r, _BILLING_KEEP),
             'customers': [_pick(c, _CUSTOMER_KEEP) for c in (r.get('customers') or [])]}
            for r in (d.get('billing') or [])
        ],
        'users':   [_pick(u, _USER_KEEP) for u in (d.get('users') or [])],
        'trend':   [{'month': t.get('month'), 'total': t.get('total')}
                    for t in (d.get('trend') or [])],
    }


def _sanitize_person(p):
    if not p:
        return None
    return {
        'info':     _pick(p.get('info') or {}, _PERSON_INFO_KEEP),
        'bills':    [_pick(b, _BILL_KEEP) for b in (p.get('bills') or [])],
        'services': [_pick(s, _PERSON_SVC_KEEP) for s in (p.get('services') or [])],
    }


_MGMT_FAC_KEEP = {'name', 'total', 'items'}
_MGMT_ITEM_KEEP = {'code', 'name', 'amount'}

# ── Sanitizers for the remaining tabs / modals ───────────────────────────────
_FAC_BILL_KEEP  = {'name', 'total', 'insurance', 'self_pay', 'category', 'categories',
                   'active', 'rev_by_cat', 'trend'}
_INS_PERSON_KEEP = {'person_id', 'full_name', 'inferred_level', 'monthly_total',
                    'limit_yen', 'unused_yen', 'util_pct'}
_INS_PERSON_KEEP_FULL = _INS_PERSON_KEEP | {'residence', 'care_ins_yen',
                                            'unit_price', 'exceeded_yen'}
_ANALYSIS_FAC_KEEP = {'office_name', 'category', 'region', 'active', 'revenue',
                      'avg_revenue', 'avg_util', 'ins_count', 'low_util_count',
                      'total_potential', 'has_limit_analysis', 'unit_price', 'type'}
_PERSON_LITE_KEEP   = {'person_id', 'full_name', 'residence', 'service_count',
                       'total', 'insurance', 'self_pay'}
_USER_LITE_KEEP     = {'person_id', 'full_name', 'amount', 'insurance',
                       'self_pay', 'provider_name'}
_RESIDENT_LITE_KEEP = {'person_id', 'full_name', 'amount', 'insurance', 'self_pay',
                       'sales_type', 'billing_facility'}
_FAC_DETAIL_BILL_KEEP = {'category', 'service_name', 'total', 'insurance', 'self_pay'}
_CROSS_TARGET_KEEP  = {'person_id', 'full_name', 'residence', 'service_count',
                       'monthly_total', 'missing'}


def _sanitize_mgmt_fac(f):
    if not f:
        return None
    out = _pick(f, _MGMT_FAC_KEEP)
    out['items'] = [_pick(i, _MGMT_ITEM_KEEP) for i in (f.get('items') or [])]
    return out


def _trim_analysis_fac(f):
    """Trim an analysis-tab facility (居宅系/施設系/サービス拠点)
    to keep top 5 ins_persons by amount, drop the bulky ones."""
    if not f: return None
    x = _pick(f, _ANALYSIS_FAC_KEEP)
    if 'ins_persons' in (f or {}):
        ins = [_pick(p, _INS_PERSON_KEEP) for p in (f.get('ins_persons') or [])]
        ins.sort(key=lambda p: -(p.get('monthly_total') or 0))
        x['ins_persons_top5'] = ins[:5]
    return x


def _sanitize_facilities_billing(rows):
    if not rows: return []
    return [_pick(f, _FAC_BILL_KEEP) for f in rows]


def _sanitize_analysis(d):
    if not d: return None
    out = {
        'total_ins_all': d.get('total_ins_all'),
        'total_ins_persons': d.get('total_ins_persons'),
        'total_low_util': d.get('total_low_util'),
        'total_potential': d.get('total_potential'),
        'avg_tenure': d.get('avg_tenure'),
        'care_level_distribution': d.get('care_level_distribution'),
        'categories': d.get('categories'),
    }
    out['facilities'] = [_trim_analysis_fac(f) for f in (d.get('facilities') or [])]
    out['inst_facilities'] = [_trim_analysis_fac(f) for f in (d.get('inst_facilities') or [])]
    out['svc_facilities'] = [_trim_analysis_fac(f) for f in (d.get('svc_facilities') or [])]
    return out


def _sanitize_cross_sell(d):
    if not d: return None
    targets = d.get('targets') or []
    targets_sorted = sorted(
        [_pick(t, _CROSS_TARGET_KEEP) for t in targets],
        key=lambda t: -(t.get('monthly_total') or 0)
    )[:50]
    return {
        'summary': d.get('summary'),
        'service_density': d.get('service_density'),
        'targets_top50': targets_sorted,
    }


def _sanitize_persons_summary(rows):
    """1881 行の persons.json → 上位 50 + 集計サマリ + サービス利用数分布."""
    if not rows: return None
    persons = list(rows)
    total_amount = sum((p.get('total') or 0) for p in persons)
    total_ins    = sum((p.get('insurance') or 0) for p in persons)
    total_self   = sum((p.get('self_pay') or 0) for p in persons)
    sc_dist = {}
    for p in persons:
        sc = p.get('service_count', 0) or 0
        sc_dist[sc] = sc_dist.get(sc, 0) + 1
    top50 = sorted(persons, key=lambda p: -(p.get('total') or 0))[:50]
    return {
        'period_summary': {
            'total_persons': len(persons),
            'total_amount': total_amount,
            'total_insurance': total_ins,
            'total_self_pay': total_self,
            'avg_amount': round(total_amount / max(len(persons), 1)),
        },
        'top_persons': [_pick(p, _PERSON_LITE_KEEP) for p in top50],
        'service_count_distribution': sc_dist,
    }


def _sanitize_facility_detail(d):
    """facility_details[name] → keep billing rows + top 30 residents/users."""
    if not d: return None
    return {
        'billing': [_pick(r, _FAC_DETAIL_BILL_KEEP) for r in (d.get('billing') or [])],
        'billing_entities': d.get('billing_entities'),
        'residents_top30': sorted(
            [_pick(r, _RESIDENT_LITE_KEEP) for r in (d.get('residents') or [])],
            key=lambda r: -(r.get('amount') or 0)
        )[:30],
        'users_top30': sorted(
            [_pick(u, _USER_LITE_KEEP) for u in (d.get('users') or [])],
            key=lambda u: -(u.get('amount') or 0)
        )[:30],
    }


def _sanitize_trend_history(d, months=None):
    """Sanitize root-level trend_history. Optionally clip rows to `months`.

    The store keeps ~16 months; the 推移 tab respects the period selector,
    so the AI scope must too — otherwise the AI sees a longer window than
    the user is looking at.
    """
    if not d: return None
    cats = d.get('categories') or []
    facs = d.get('facilities') or []
    if months:
        keep = set(months)
        cats = [r for r in cats if r.get('service_month') in keep]
        facs = [r for r in facs if r.get('service_month') in keep]
    # facilities is a flat list of {service_month, office_name, total}; rank
    # by office sum across the (period-restricted) rows
    by_office = {}
    for r in facs:
        n = r.get('office_name')
        if n: by_office[n] = by_office.get(n, 0) + (r.get('total') or 0)
    top30_offices = set([k for k, _v in sorted(by_office.items(), key=lambda kv: -kv[1])[:30]])
    facs_top30 = [r for r in facs if r.get('office_name') in top30_offices]
    return {'categories': cats, 'facilities_top30': facs_top30}


def _ai_data_for_scope(store, scope, entity_id, months, filters=None):
    """Build the per-month, sanitized data slice for a given scope.
    Sanitizers above NEVER mutate the store's cached refs — they always return
    fresh dicts/lists. Filtering happens later in `_apply_filters` (which also
    deepcopies before mutating its working copy)."""
    if scope == 'tab:card-view':
        return {
            'facility_cards': {
                m: [_sanitize_card(c) for c in (store.get(f'{m}/facility_cards') or [])]
                for m in months
            },
            'haifu': {
                m: [_sanitize_haifu_fac(f) for f in (store.get(f'{m}/haifu') or [])]
                for m in months
            },
        }
    if scope == 'modal:card-detail':
        per_month_card, per_month_haifu = {}, {}
        for m in months:
            cards = store.get(f'{m}/facility_cards_detail') or {}
            per_month_card[m] = _sanitize_card_detail(
                cards.get(entity_id) if isinstance(cards, dict) else None
            )
            haifu = store.get(f'{m}/haifu') or []
            per_month_haifu[m] = _sanitize_haifu_fac(
                next((f for f in haifu if f.get('name') == entity_id), None)
            )
        return {'card_per_month': per_month_card, 'haifu_per_month': per_month_haifu}
    if scope == 'modal:person':
        return {
            'person_per_month': {
                m: _sanitize_person((store.get(f'{m}/person_details') or {}).get(entity_id))
                for m in months
            }
        }
    if scope == 'tab:card-view2':
        # Same as tab:card-view + management-accounting journal layer
        return {
            'facility_cards': {
                m: [_sanitize_card(c) for c in (store.get(f'{m}/facility_cards') or [])]
                for m in months
            },
            'haifu': {
                m: [_sanitize_haifu_fac(f) for f in (store.get(f'{m}/haifu') or [])]
                for m in months
            },
            'mgmt_journal': {
                m: [_sanitize_mgmt_fac(f) for f in (store.get(f'{m}/mgmt_journal') or [])]
                for m in months
            },
        }
    if scope == 'modal:card-detail2':
        per_month_card, per_month_haifu, per_month_mgmt = {}, {}, {}
        for m in months:
            cards = store.get(f'{m}/facility_cards_detail') or {}
            per_month_card[m] = _sanitize_card_detail(
                cards.get(entity_id) if isinstance(cards, dict) else None
            )
            haifu = store.get(f'{m}/haifu') or []
            per_month_haifu[m] = _sanitize_haifu_fac(
                next((f for f in haifu if f.get('name') == entity_id), None)
            )
            mgmt = store.get(f'{m}/mgmt_journal') or []
            per_month_mgmt[m] = _sanitize_mgmt_fac(
                next((f for f in mgmt if f.get('name') == entity_id), None)
            )
        return {
            'card_per_month': per_month_card,
            'haifu_per_month': per_month_haifu,
            'mgmt_per_month': per_month_mgmt,
        }

    # ── Tabs (other than the card-view ones above) ─────────────────────────
    if scope == 'global':
        return {
            'monthly_kpi': [
                {'_month': m, **((store.get(f'{m}/kpi') or {}))} for m in months
            ],
            'monthly_alerts': [
                {'_month': m, **((store.get(f'{m}/alerts') or {}))} for m in months
            ],
        }
    if scope == 'tab:facilities':
        return {
            'facilities_billing': {
                m: _sanitize_facilities_billing(store.get(f'{m}/facilities_billing') or [])
                for m in months
            }
        }
    if scope == 'tab:facilities-compare':
        # Live snapshot of the 拠点比較 picker. Mirrors the chart's value logic
        # (category filter / per-user / 相対値) so the AI sees exactly what's
        # plotted right now.
        f = filters or {}
        selected = (f.get('selected') or [])[:30]
        cat = f.get('category') or None  # None = 全カテゴリ合計
        per_user = bool(f.get('per_user'))
        y_axis = f.get('y_axis') or 'abs'

        # Get the latest snapshot month available for trend stitching
        meta = store.get('months') or []
        all_months = sorted(m if isinstance(m, str) else m.get('month') for m in meta)

        series = {}
        for name in selected:
            # Stitch trend rows across snapshots for months in the selected range
            rows_by_month = {}
            for snap in reversed(all_months):
                trends = (store.get(f'{snap}/facility_trends') or {}).get(name) or []
                for row in trends:
                    sm = row.get('service_month')
                    if sm in months and sm not in rows_by_month:
                        rows_by_month[sm] = row
                if all_months and snap <= (months[0] if months else snap):
                    break

            points = []
            for m in months:
                row = rows_by_month.get(m)
                if not row:
                    points.append({'_month': m, 'value': None})
                    continue
                bc = row.get('by_cat') or {}
                v = sum((x or 0) for x in bc.values()) if cat is None else (bc.get(cat) or 0)
                residents = row.get('residents') or 0
                if per_user:
                    v = round(v / residents) if residents > 0 else None
                points.append({'_month': m, 'value': v, 'residents': residents})

            if y_axis == 'rel':
                base = next((p['value'] for p in points
                             if p.get('value') is not None and p['value'] > 0), None)
                if base:
                    for p in points:
                        if p.get('value') is not None:
                            p['value_rel_pct'] = round(p['value'] / base * 1000) / 10

            series[name] = points

        return {
            'mode': {
                'category': cat or '全カテゴリ合計',
                'per_user': per_user,
                'y_axis': y_axis,
            },
            'series': series,
        }
    if scope == 'tab:analysis':
        return {
            'analysis': {m: _sanitize_analysis(store.get(f'{m}/analysis')) for m in months},
            'cross_sell': {m: _sanitize_cross_sell(store.get(f'{m}/cross_sell')) for m in months},
        }
    if scope == 'tab:services':
        return {
            'services': {m: store.get(f'{m}/services') or [] for m in months}
        }
    if scope == 'tab:persons':
        return {
            'persons': {m: _sanitize_persons_summary(store.get(f'{m}/persons') or []) for m in months}
        }
    if scope == 'tab:history':
        # trend_history is a single root-level file (not per-month) — clip to
        # the selected period so the AI sees the same window as the chart.
        return {'trend_history': _sanitize_trend_history(store.get('trend_history') or {}, months)}

    if scope == 'tab:map':
        map_rows = []
        for m in months:
            rows = store.get(f'{m}/map') or []
            for r in rows:
                r['_month'] = m
                map_rows.append(r)
        return {'facilities': map_rows}

    # ── Drill-down modals beyond card-view family ──────────────────────────
    if scope == 'modal:facility':
        return {
            'facility_per_month': {
                m: _sanitize_facility_detail((store.get(f'{m}/facility_details') or {}).get(entity_id))
                for m in months
            }
        }
    if scope == 'modal:care-level':
        # Collect residents matching the level across 居宅系 facilities.
        # Special-case '自費のみ' uses self_pay_persons (no insurance fields).
        per_month = {}
        is_self_pay = (entity_id == '自費のみ')
        for m in months:
            a = store.get(f'{m}/analysis') or {}
            matches = []
            for f in (a.get('facilities') or []):
                src = f.get('self_pay_persons') if is_self_pay else f.get('ins_persons')
                for p in (src or []):
                    if not is_self_pay and (p.get('inferred_level') or '') != entity_id:
                        continue
                    item = _pick(p, _INS_PERSON_KEEP_FULL)
                    item['_facility'] = f.get('office_name')
                    matches.append(item)
            matches.sort(key=lambda p: -(p.get('monthly_total') or 0))
            per_month[m] = matches[:50]
        return {'care_level': entity_id, 'persons_per_month': per_month}
    if scope == 'modal:service-users':
        # entity_id = sales_type. Aggregate users across all facilities/months
        # with that sales_type. Mirrors /api/service-users semantics so the AI
        # sees the same list the user is looking at.
        svc = entity_id or ''
        acc = {}
        for m in months:
            all_details = store.get(f'{m}/person_details') or {}
            for aid, d in all_details.items():
                info = d.get('info') or {}
                hits = [b for b in (d.get('bills') or []) if b.get('sales_type') == svc]
                if not hits:
                    continue
                amount = sum(b.get('amount', 0) or 0 for b in hits)
                insurance = sum(b.get('insurance', 0) or 0 for b in hits)
                self_pay = sum(b.get('self_pay', 0) or 0 for b in hits)
                facs = {b.get('facility', '') for b in hits if b.get('facility')}
                if aid not in acc:
                    acc[aid] = {'person_id': aid, 'full_name': info.get('full_name'),
                                'amount': 0, 'insurance': 0, 'self_pay': 0, '_facs': set()}
                acc[aid]['amount'] += amount
                acc[aid]['insurance'] += insurance
                acc[aid]['self_pay'] += self_pay
                acc[aid]['_facs'] |= facs
                if info.get('full_name'):
                    acc[aid]['full_name'] = info.get('full_name')
        users = []
        for aid, e in acc.items():
            users.append({
                'person_id': aid, 'full_name': e['full_name'],
                'amount': round(e['amount']), 'insurance': round(e['insurance']),
                'self_pay': round(e['self_pay']),
                'facilities': sorted([f for f in e['_facs'] if f])[:5],
            })
        users.sort(key=lambda u: -(u.get('amount') or 0))
        return {'service': svc, 'users_top30': users[:30], 'total_users': len(users),
                'total_amount': sum(u['amount'] for u in users)}
    if scope == 'modal:fac-gap':
        per_month = {}
        for m in months:
            a = store.get(f'{m}/analysis') or {}
            match = None
            for grp_key in ('facilities', 'inst_facilities', 'svc_facilities'):
                for f in (a.get(grp_key) or []):
                    if f.get('office_name') == entity_id:
                        match = _trim_analysis_fac(f)
                        match['_group'] = grp_key
                        break
                if match:
                    break
            per_month[m] = match
        return {'gap_per_month': per_month}

    return {}


def _apply_filters(data, scope, filters):
    """Apply the dashboard's UI-side filter state to the data slice.

    The `data` argument is a *fresh* dict produced by `_ai_data_for_scope` (its
    sanitizers never share refs with the store). To keep the contract crystal
    clear and immune to future refactors of those sanitizers, we still
    deepcopy here before mutating.
    """
    if not filters:
        return data
    data = copy.deepcopy(data)

    if scope == 'tab:card-view':
        # Multi-select chips. `categories` is a list. Empty list means "全て".
        # Filter is FACILITY-LEVEL only (matches frontend behaviour): drop
        # facilities that don't match any selected cat, keep matching ones
        # with their FULL data (services / haifu unchanged).
        # Legacy `category` (single string) accepted for backwards compat.
        cats_set = set(filters.get('categories') or [])
        legacy_cat = filters.get('category')
        if legacy_cat and legacy_cat != 'all':
            cats_set.add(legacy_cat)
        if cats_set:
            def _haifu_match_cats(h):
                if not h:
                    return False
                for r in (h.get('haifu_in') or []):
                    if categorize_service(r.get('service', '')) in cats_set:
                        return True
                for r in (h.get('haifu_out') or []):
                    if categorize_service(r.get('service', '')) in cats_set:
                        return True
                return False
            # Build per-month allowed name set: keep any facility with any
            # matching service or haifu row.
            for m, fac_list in (data.get('facility_cards') or {}).items():
                if not fac_list:
                    continue
                hf_list = (data.get('haifu') or {}).get(m) or []
                hf_by_name = {h['name']: h for h in hf_list if h}
                fac_list[:] = [
                    f for f in fac_list
                    if f and (
                        any(s.get('category') in cats_set for s in (f.get('services') or []))
                        or _haifu_match_cats(hf_by_name.get(f.get('name')))
                    )
                ]
            # Drop haifu records for filtered-out facilities to keep AI input
            # consistent with what the user sees.
            for m, hf_list in (data.get('haifu') or {}).items():
                if not hf_list:
                    continue
                fc_names = {f.get('name') for f in (data.get('facility_cards') or {}).get(m) or [] if f}
                hf_list[:] = [h for h in hf_list if h and h.get('name') in fc_names]
        return data

    if scope == 'modal:card-detail':
        cat_mutes   = set(filters.get('cat_mutes')   or [])
        row_mutes   = set(filters.get('row_mutes')   or [])
        cust_mutes  = set(filters.get('cust_mutes')  or [])
        haifu_mutes = set(filters.get('haifu_mutes') or [])
        for _m, card in (data.get('card_per_month') or {}).items():
            if not card:
                continue
            new_billing = []
            for i, r in enumerate(card.get('billing') or []):
                if i in row_mutes:
                    continue
                if r.get('category') in cat_mutes:
                    continue
                if r.get('customers'):
                    r['customers'] = [c for c in r['customers']
                                      if f"{i}|{c.get('name')}" not in cust_mutes]
                new_billing.append(r)
            card['billing'] = new_billing
            card['total']   = sum((r.get('amount') or 0) for r in new_billing)
        for _m, h in (data.get('haifu_per_month') or {}).items():
            if not h:
                continue
            h['haifu_in']  = [r for i, r in enumerate(h.get('haifu_in')  or [])
                              if f"in-{i}"  not in haifu_mutes]
            h['haifu_out'] = [r for i, r in enumerate(h.get('haifu_out') or [])
                              if f"out-{i}" not in haifu_mutes]
            h['haifu_in_total']  = sum((r.get('amount') or 0) for r in h['haifu_in'])
            h['haifu_out_total'] = sum((r.get('amount') or 0) for r in h['haifu_out'])
        return data

    if scope == 'modal:person':
        cat_mutes = set(filters.get('cat_mutes') or [])
        row_mutes = set(filters.get('row_mutes') or [])
        for _m, p in (data.get('person_per_month') or {}).items():
            if not p:
                continue
            new_bills = []
            for i, b in enumerate(p.get('bills') or []):
                if i in row_mutes:
                    continue
                if categorize_service(b.get('sales_type', '')) in cat_mutes:
                    continue
                new_bills.append(b)
            p['bills'] = new_bills
        return data

    if scope == 'tab:facilities':
        # Multi-select chip: facility-level filter. Drop facilities whose
        # categories don't intersect the selected set.
        cats_set = set(filters.get('categories') or [])
        if cats_set:
            for _m, fac_list in (data.get('facilities_billing') or {}).items():
                if not fac_list:
                    continue
                fac_list[:] = [
                    f for f in fac_list
                    if f and (
                        # 'category' is composite like '有料+デイ' so split
                        any(c.strip() in cats_set
                            for c in (f.get('category') or '').split('+'))
                        or (set(f.get('categories') or []) & cats_set)
                    )
                ]
        return data

    if scope == 'tab:services':
        # Single-select chip ('all' or category) + sort mode. Drop items not
        # matching the category so the AI sees the same slice as the chart.
        cat = filters.get('category')
        if cat and cat != 'all':
            for _m, items in (data.get('services') or {}).items():
                if not items:
                    continue
                items[:] = [s for s in items if s and s.get('category') == cat]
        # Sort mode is informational only — pass through as a key the AI sees.
        if filters.get('sort'):
            data['_sort'] = filters['sort']
        return data

    if scope == 'tab:persons':
        # Search box filter — when present, the user is honing in on a slice.
        # Apply to top_persons and recompute the period_summary for that slice.
        q = (filters.get('q') or '').strip().lower()
        if q:
            for _m, p in (data.get('persons') or {}).items():
                if not p:
                    continue
                top = p.get('top_persons') or []
                top[:] = [
                    r for r in top
                    if q in (r.get('full_name') or '').lower()
                    or q in (r.get('residence') or '').lower()
                    or q in str(r.get('person_id') or '').lower()
                ]
        return data

    if scope == 'tab:history':
        # mode = 'category' or 'facility'. In category mode, only include the
        # active categories; in facility mode the user isn't toggling cats so
        # show all + drop categories series to focus the AI on facilities.
        mode = filters.get('mode') or 'category'
        active = set(filters.get('categories') or [])
        th = data.get('trend_history') or {}
        if mode == 'category':
            if active and th.get('categories'):
                th['categories'] = [c for c in th['categories'] if c.get('category') in active]
            th.pop('facilities_top30', None)
        else:  # facility mode — drop the categories slice
            th.pop('categories', None)
        data['_view_mode'] = mode
        return data

    if scope == 'tab:map':
        mcat = filters.get('category')
        if mcat and mcat != 'all':
            data['facilities'] = [r for r in data.get('facilities', []) if mcat in (r.get('category') or '')]
        data['facilities'] = sorted(data.get('facilities', []), key=lambda r: -(r.get('revenue') or 0))[:30]
        return data

    return data


def _filters_hash(filters):
    if not filters:
        return ''
    s = _json.dumps(filters, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(s.encode()).hexdigest()[:12]


def _build_meta(scope, entity_id, period_label, filters):
    """Display string showing what AI saw — for transparency in the UI."""
    model_name = os.environ.get('DEEPSEEK_MODEL', os.environ.get('OPENAI_MODEL', 'AI'))
    parts = [model_name, f'期間: {period_label}']
    if entity_id:
        parts.append(entity_id)
    f = filters or {}
    if f.get('category') and f['category'] != 'all':
        parts.append(f"カテゴリ: {f['category']}")
    n_mutes = sum(len(f.get(k) or []) for k in
                  ('cat_mutes', 'row_mutes', 'cust_mutes', 'haifu_mutes'))
    if n_mutes:
        parts.append(f'除外: {n_mutes}項目')
    return ' / '.join(parts)


@app.route('/api/ai-analysis', methods=['POST'])
@login_required
def ai_analysis():
    """Streaming endpoint. Returns NDJSON: each line is a JSON object with
    `type`: 'meta' | 'chunk' | 'done' | 'error'.

    Frontend reads via fetch + ReadableStream. Cached responses replay as a
    single chunk so the client code path is uniform.
    """
    from flask import Response, stream_with_context

    body = request.get_json() or {}
    scope = body.get('scope') or ''
    entity_id = body.get('entity_id') or ''
    filters = body.get('filters') or {}
    months = _period_months()
    if not months:
        return jsonify({'error': 'no_period'}), 400

    user = (session.get('user') or {}).get('email', 'anon')

    now = time.time()
    bucket = _AI_RATE.setdefault(user, [])
    bucket[:] = [t for t in bucket if now - t < _AI_RATE_WINDOW]
    if len(bucket) >= _AI_RATE_MAX:
        return jsonify({'error': 'rate_limited'}), 429
    # Global cap — prevents Sybil attack (rotating accounts to bypass per-user limit)
    _AI_GLOBAL_RATE[:] = [t for t in _AI_GLOBAL_RATE if now - t < _AI_RATE_WINDOW]
    if len(_AI_GLOBAL_RATE) >= _AI_GLOBAL_MAX:
        return jsonify({'error': 'global_rate_limited'}), 429
    _AI_GLOBAL_RATE.append(now)
    bucket.append(now)

    fhash = _filters_hash(filters)
    cache_key = (scope, entity_id, tuple(months), fhash)

    s = _get_store()
    raw = _ai_data_for_scope(s, scope, entity_id, months, filters)
    if not raw:
        return jsonify({'error': 'no_data_for_scope'}), 400

    data = _apply_filters(raw, scope, filters)
    # Final step: rename English JSON keys to Japanese so the model has no
    # English identifiers in the payload to echo back ("native", "haifu_in"...)
    data = _translate_keys_to_ja(data)

    period_label = months[0] if len(months) == 1 else f'{months[0]} 〜 {months[-1]}'
    meta = _build_meta(scope, entity_id, period_label, filters)

    def ndjson(obj):
        return _json.dumps(obj, ensure_ascii=False) + '\n'

    cached_entry = _AI_CACHE.get(cache_key)
    is_cached = bool(cached_entry and cached_entry[1] > now)

    def generate():
        # 1. meta line (sent immediately so client can show context even
        #    while the model is still streaming)
        yield ndjson({'type': 'meta', 'meta': meta, 'cached': is_cached})

        if is_cached:
            yield ndjson({'type': 'chunk', 'text': cached_entry[0]})
            yield ndjson({'type': 'done'})
            return

        accumulated = []
        try:
            import ai_analyze
            for chunk in ai_analyze.analyze_stream(scope, data, period_label, entity_id, filters):
                accumulated.append(chunk)
                yield ndjson({'type': 'chunk', 'text': chunk})
        except Exception as e:
            print(f'AI error scope={scope} {type(e).__name__}: {e}')
            yield ndjson({'type': 'error', 'message': f'{type(e).__name__}: {e}'})
            return

        full_text = ''.join(accumulated).strip()
        # Evict expired then insert new
        expired = [k for k, v in _AI_CACHE.items() if v[1] < now]
        for k in expired:
            del _AI_CACHE[k]
        _AI_CACHE[cache_key] = (full_text, now + _AI_TTL, meta)

        print(f'AI ok scope={scope} entity={entity_id} user={user} '
              f'chars={len(full_text)} fhash={fhash} cached_size={len(_AI_CACHE)}')
        yield ndjson({'type': 'done'})

    resp = Response(stream_with_context(generate()), mimetype='application/x-ndjson')
    # Make sure Cloud Run / gunicorn don't buffer the stream
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['X-Accel-Buffering'] = 'no'
    return resp


# ── AI Chat (general-purpose, tool-enabled) ──────────────────────────────────

@app.route('/api/ai-chat', methods=['POST'])
@login_required
def ai_chat():
    """Streaming chat endpoint. Accepts {messages: [{role, content}, ...]}.
    Returns NDJSON with type: chunk | tool_call | done | error."""
    from flask import Response, stream_with_context

    body = request.get_json() or {}
    messages = body.get('messages') or []

    user = (session.get('user') or {}).get('email', 'anon')

    # Rate limit: 30 calls/hour/user
    now = time.time()
    bucket = _AI_RATE.setdefault(user, [])
    bucket[:] = [t for t in bucket if now - t < _AI_RATE_WINDOW]
    if len(bucket) >= _AI_RATE_MAX:
        return jsonify({'error': 'rate_limited'}), 429
    # Global cap — prevents Sybil attack (rotating accounts to bypass per-user limit)
    _AI_GLOBAL_RATE[:] = [t for t in _AI_GLOBAL_RATE if now - t < _AI_RATE_WINDOW]
    if len(_AI_GLOBAL_RATE) >= _AI_GLOBAL_MAX:
        return jsonify({'error': 'global_rate_limited'}), 429
    _AI_GLOBAL_RATE.append(now)
    bucket.append(now)

    # Init ai_chat store reference on first call
    import ai_chat as _chat
    if not getattr(_chat, '_store', None):
        _chat.init(_get_store())

    def generate():
        try:
            for line in _chat.chat_stream(messages):
                yield line
        except Exception as e:
            print(f'Chat error user={user} {type(e).__name__}: {e}')
            yield _json.dumps({'type': 'error', 'message': f'{type(e).__name__}: {e}'}, ensure_ascii=False) + '\n'

    resp = Response(stream_with_context(generate()), mimetype='application/x-ndjson')
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['X-Accel-Buffering'] = 'no'
    return resp


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
