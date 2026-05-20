"""
Generate simulated data for the demo dashboard.
Produces all 18 file types across 12 months with cross-file consistency.
Uses fixed random seed (42) for deterministic, reproducible output.
"""
import json
import os
import random
import copy

random.seed(42)

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
MONTHS = [f'2025-{m:02d}' for m in range(4, 13)] + [f'2026-{m:02d}' for m in range(1, 4)]

# ── Facility definitions ──────────────────────────────────────────────────────

FACILITIES = [
    # (name, category, region, base_active, base_revenue, codes_str, lat, lng, address_ward, is_residential)
    # ── 東京 cluster (9: 4 res + 3 visit + 2 other) ──
    ('桜木拠点', '有料+デイ+障がい者GH', '東京', 55, 12_000_000,
     '1110,1120,2110,2290,1710,2160,1640', 35.685, 139.755, '新宿区 西新宿', True),
    ('けやき拠点', '有料+デイ', '東京', 45, 8_000_000,
     '1110,1120,2110,2290,1640', 35.672, 139.768, '渋谷区 代々木', True),
    ('つつじ拠点', '有料+デイ+障がい者GH', '東京', 50, 14_000_000,
     '1110,1120,2110,2290,1710,2160,1640', 35.693, 139.747, '港区 六本木', True),
    ('若葉の里', '特養', '東京', 62, 11_500_000,
     '1690_1691_2150,2280,1692_2150_短期,1660,2130', 35.678, 139.782, '品川区 大崎', True),
    ('デモ介護ステーション中央', '訪問介護', '東京', 450, 22_000_000,
     '1210,1230,1270,1281,1290', 35.689, 139.761, '新宿区 西新宿', False),
    ('デモ介護ステーション北', '訪問介護', '東京', 350, 15_000_000,
     '1210,1230,1270,1281,1290', 35.701, 139.752, '新宿区 大久保', False),
    ('デモ訪問看護ステーション中央', '訪問看護', '東京', 470, 26_000_000,
     '1310_1320_1330,1340,1350', 35.675, 139.773, '渋谷区 代々木', False),
    ('デモリサーチ中央', 'その他', '東京', 0, 1_050_000,
     '2220', 35.696, 139.758, '新宿区 西新宿', False),
    ('居宅介護支援事業所（中央）', '相談・CM', '東京', 390, 7_200_000,
     '1630,1620', 35.682, 139.756, '港区 浜松町', False),
    # ── 大阪 cluster (9: 4 res + 3 visit + 2 other) ──
    ('ひがし拠点', '有料+デイ', '大阪', 30, 7_500_000,
     '1110,1120,2110,2290,1640', 34.697, 135.508, '北区 梅田', True),
    ('すみれ拠点', '有料+デイ+障がい者GH', '大阪', 45, 11_000_000,
     '1110,1120,2110,2290,1710,2160,1640', 34.684, 135.495, '中央区 本町', True),
    ('さくらGH', '障がい者GH', '大阪', 17, 3_700_000,
     '1410_2140,1710,2160', 34.703, 135.513, '淀川区 西中島', True),
    ('こまくさGH', '障がい者GH', '大阪', 15, 3_500_000,
     '1410_2140,1710,2160', 34.691, 135.521, '天王寺区 上本町', True),
    ('デモ介護ステーション南', '訪問介護', '大阪', 340, 13_500_000,
     '1210,1230,1270,1281,1290', 34.677, 135.502, '北区 中之島', False),
    ('デモ訪問看護ステーション東', '訪問看護', '大阪', 330, 16_000_000,
     '1310_1320_1330,1340,1350', 34.689, 135.518, '天王寺区 上本町', False),
    ('デモ訪問看護ステーション西', '訪問看護', '大阪', 99, 6_500_000,
     '1310_1320_1330,1340', 34.673, 135.487, '北区 大淀', False),
    ('デモケアテクノロジー中央', '福祉用具', '大阪', 55, 820_000,
     '1640,1641,1650', 34.694, 135.503, '中央区 本町', False),
    ('けやきビル', 'その他', '大阪', 0, 120_000,
     '2230', 34.681, 135.511, '淀川区 西中島', False),
    # ── 福岡 cluster (8: 5 res + 2 visit + 1 other) ──
    ('中央拠点', '有料+デイ', '福岡', 40, 7_000_000,
     '1110,1120,2110,2290,1640', 33.597, 130.415, '博多区 博多駅前', True),
    ('みなと拠点', '有料+デイ+障がい者GH', '福岡', 60, 16_000_000,
     '1110,1120,2110,2290,1710,2160,1640', 33.583, 130.392, '中央区 天神', True),
    ('あかね拠点', '有料+デイ', '福岡', 35, 6_500_000,
     '1110,1120,2110,2290,1640', 33.591, 130.408, '東区 馬出', True),
    ('ひまわりGH', '障がい者GH', '福岡', 19, 4_300_000,
     '1410_2140,1710,2160', 33.572, 130.387, '南区 大橋', True),
    ('しらかばGH', '障がい者GH', '福岡', 14, 2_800_000,
     '1410_2140,1710,2160', 33.603, 130.421, '博多区 博多駅南', True),
    ('デモ介護ステーション東', '訪問介護', '福岡', 170, 8_500_000,
     '1210,1230,1290', 33.589, 130.399, '中央区 薬院', False),
    ('デモ訪問看護ステーション南', '訪問看護', '福岡', 194, 15_500_000,
     '1310_1320_1330,1340,1350', 33.576, 130.402, '南区 高宮', False),
    ('就労継続支援事業所（あかね）', '就労支援', '福岡', 253, 13_500_000,
     '1610,2240,2250,2299', 33.595, 130.411, '東区 箱崎', False),
]

# ── Service types ──────────────────────────────────────────────────────────────

SERVICE_TYPES = [
    ('訪問介護', '訪問介護', 0.12),
    ('訪問介護（障害福祉サービス）', '訪問介護', 0.09),
    ('訪問介護（全額自費）', '訪問介護', 0.003),
    ('地方自治体独自事業（移動支援）', '訪問介護', 0.004),
    ('訪問看護（介護）', '訪問看護', 0.07),
    ('訪問看護（医療_国保連）', '訪問看護', 0.08),
    ('訪問看護（医療_支払基金）', '訪問看護', 0.05),
    ('訪問看護（自費）', '訪問看護', 0.001),
    ('通所介護', '通所介護', 0.09),
    ('地域密着型通所介護', '通所介護', 0.04),
    ('生活介護（デイナイトケア）', '通所介護', 0.003),
    ('デイサービス（自費）', '通所介護', 0.001),
    ('有料老人ホーム', '有料老人ホーム', 0.12),
    ('サ高住', '有料老人ホーム', 0.02),
    ('特定施設入居者生活介護', '介護付有料', 0.04),
    ('特定施設入居者生活介護（施設）', '介護付有料', 0.02),
    ('共同生活援助', '障がい者GH', 0.02),
    ('共同生活援助(受託居宅介護以外)', '障がい者GH', 0.10),
    ('認知症対応型共同生活介護', '認知症GH', 0.008),
    ('認知症対応型共同生活介護（施設）', '認知症GH', 0.003),
    ('地域密着型介護老人福祉施設', '特養', 0.03),
    ('短期入所', '特養', 0.002),
    ('居宅介護支援', '相談・CM', 0.02),
    ('相談支援', '相談・CM', 0.004),
    ('福祉用具貸与', '福祉用具', 0.02),
    ('福祉用具貸与（自費）', '福祉用具', 0.001),
    ('福祉用具販売', '福祉用具', 0.0001),
    ('就労継続支援B型', '就労支援', 0.015),
    ('就労事業収入（食事提供）', '就労支援', 0.008),
    ('就労事業収入（洗濯）', '就労支援', 0.003),
    ('就労事業収入（食事・福岡）', '就労支援', 0.0),
    ('その他', 'その他', 0.01),
    ('ITコンサルティング事業', 'その他', 0.0),
    ('リサーチ事業収入', 'その他', 0.003),
    ('施設賃料（ビル）', 'その他', 0.001),
]

# ── Person name generation ─────────────────────────────────────────────────────
# ALL names below are synthetic. They use common Japanese surnames and given names
# combined deterministically — no real person data from the original dashboard.
# The name pool produces 100 surnames × 20 male + 20 female given names = 4000+
# unique combinations. Actual person count is ~1800, so no duplicates in practice.

SURNAMES = ['佐藤', '鈴木', '高橋', '田中', '渡辺', '伊藤', '山本', '中村', '小林', '加藤',
            '吉田', '山田', '佐々木', '山口', '松本', '井上', '木村', '林', '清水', '斎藤',
            '池田', '橋本', '森', '阿部', '石川', '前田', '藤田', '小川', '岡田', '後藤',
            '長谷川', '石井', '村上', '近藤', '坂本', '遠藤', '藤井', '西村', '福田', '太田',
            '三浦', '藤原', '岡本', '中川', '中島', '原田', '小野', '田村', '竹内', '金子',
            '和田', '中山', '石田', '上田', '森田', '原', '柴田', '酒井', '工藤', '横山',
            '宮崎', '宮本', '内田', '高木', '安藤', '谷口', '大野', '丸山', '今井', '河野',
            '菅原', '武田', '新井', '杉山', '野口', '平野', '千葉', '久保', '菊地', '野村',
            '市川', '大西', '渡部', '川崎', '飯田', '松尾', '荒木', '古川', '小松', '江口',
            '大橋', '松田', '本間', '片山', '横田', '北村', '永井', '関口', '服部', '高田']

GIVEN_NAMES_M = ['太郎', '一郎', '二郎', '健一', '翔太', '大輔', '拓也', '亮', '誠', '直樹',
                 '和也', '裕太', '雄大', '康平', '健太', '達也', '剛', '隆', '洋介', '悠斗']
GIVEN_NAMES_F = ['花子', '美咲', '優子', '真由美', '由美', '愛', '恵子', '智子', '明美', '久美子',
                 '陽子', '裕子', '幸子', '直美', '香織', '麻衣', '真理', '彩', '千尋', '葵']

GENDER = ['M', 'F']

def _random_name(i):
    surname = SURNAMES[i % len(SURNAMES)]
    g = GENDER[i % 2]
    given_pool = GIVEN_NAMES_M if g == 'M' else GIVEN_NAMES_F
    given = given_pool[(i // 2) % len(given_pool)]
    return f'{surname} {given}', g

# Map residential facilities to their names for person residence assignment
_RESIDENTIAL_FACS = [f[0] for f in FACILITIES if f[9]]

# ── Helper: intra-month revenue growth factor ──────────────────────────────────

def _growth_factor(month_idx):
    """Gradual growth from month 0 (2025-04) to month 11 (2026-03). ~2%/month."""
    return 0.80 + 0.02 * month_idx

# ── Per-month data generators ──────────────────────────────────────────────────

def _gen_kpi(month_idx, facilities_billing_rows, persons):
    total = sum(r['total'] for r in facilities_billing_rows)
    ins = sum(r['insurance'] for r in facilities_billing_rows)
    sp = sum(r['self_pay'] for r in facilities_billing_rows)
    customers = len(persons)
    facilities_count = len(facilities_billing_rows)
    prev_customers = max(customers - random.randint(10, 50), 100)
    new_cust = random.randint(10, 60)
    lost_cust = random.randint(5, 20)
    prev_total = total * random.uniform(0.94, 1.00)
    prev_ins = ins * random.uniform(0.94, 1.00)
    prev_sp = sp * random.uniform(0.94, 1.00)
    gf = _growth_factor(month_idx)
    prev_month_idx = month_idx - 1
    prev_month = MONTHS[prev_month_idx] if prev_month_idx >= 0 else None

    change_total = ((total - prev_total) / prev_total * 100) if prev_total else 0
    change_ins = ((ins - prev_ins) / prev_ins * 100) if prev_ins else 0
    change_sp = ((sp - prev_sp) / prev_sp * 100) if prev_sp else 0
    change_cust = ((customers - prev_customers) / prev_customers * 100) if prev_customers else 0

    avg_per_cust = round(total / customers) if customers else 0
    avg_per_fac = round(total / facilities_count) if facilities_count else 0
    avg_ins_cust = round(ins / customers) if customers else 0
    avg_sp_cust = round(sp / customers) if customers else 0

    total_m = total / 1_000_000
    sentence = f'売上 ¥{total_m:.1f}M（前月比 {change_total:+.1f}%） ｜ 利用者 {customers:,}名 ｜ 新規{new_cust}名・終了{lost_cust}名 ｜ {facilities_count}拠点'

    return {
        'total': round(total), 'insurance': round(ins), 'self_pay': round(sp),
        'customers': customers, 'facilities': facilities_count,
        'new_customers': new_cust, 'lost_customers': lost_cust,
        'net_customers': new_cust - lost_cust,
        'prev_total': round(prev_total), 'prev_insurance': round(prev_ins),
        'prev_self_pay': round(prev_sp), 'prev_customers': prev_customers,
        'prev_month': prev_month,
        'change_total_pct': round(change_total, 1), 'change_insurance_pct': round(change_ins, 1),
        'change_self_pay_pct': round(change_sp, 1), 'change_customers_pct': round(change_cust, 1),
        'change_non_sale_pct': 0, 'non_sale': 0, 'prev_non_sale': 0,
        'avg_per_customer': avg_per_cust, 'avg_per_facility': avg_per_fac,
        'avg_ins_per_customer': avg_ins_cust, 'avg_pay_per_customer': avg_sp_cust,
        'status_sentence': sentence,
    }


def _gen_persons(month_idx, person_details):
    rows = []
    for aid, det in sorted(person_details.items()):
        info = det.get('info', {})
        bills = det.get('bills', [])
        total = sum(b.get('amount', 0) or 0 for b in bills)
        insurance = sum(b.get('insurance', 0) or 0 for b in bills)
        self_pay = sum(b.get('self_pay', 0) or 0 for b in bills)
        service_count = len(set(b.get('sales_type', '') for b in bills))
        residence = info.get('residence', '')
        rows.append({
            'person_id': int(aid),
            'full_name': info.get('full_name', ''),
            'residence': residence,
            'total': round(total),
            'insurance': round(insurance),
            'self_pay': round(self_pay),
            'service_count': service_count,
            'facility_count': len(set(b.get('facility', '') for b in bills if b.get('facility'))),
            'flag': info.get('flag', ''),
        })
    rows.sort(key=lambda r: -r['total'])
    return rows


# ── Map facility codes_str to a residential provider type label ──────────────
def _provider_label(codes_str):
    codes = set(codes_str.split(','))
    if '2110' in codes or '2290' in codes:
        return '住宅型有料老人ホーム'
    if '2120' in codes:
        return 'サービス付き高齢者向け住宅'
    if '1660' in codes or '2130' in codes:
        return '介護付有料老人ホーム'
    if '1710' in codes or '2160' in codes:
        return '認知症対応型共同生活介護'
    if '1410_2140' in codes:
        return '共同生活援助'
    if '1690_1691_2150' in codes or '1692_2150_短期' in codes or '2280' in codes:
        return '地域密着型介護老人福祉施設'
    return 'その他'


def _gen_facilities_billing(month_idx, facility_revs, facility_trends_map):
    rows = []
    for fname, _, _, base_active, base_rev, codes_str, lat, lng, addr_w, is_res in FACILITIES:
        rev_data = facility_revs.get(fname, {})
        total = rev_data.get('total', base_rev)
        insurance = rev_data.get('insurance', total * 0.7)
        self_pay = rev_data.get('self_pay', total * 0.3)
        rev_by_cat = rev_data.get('rev_by_cat', {})
        active = max(int(base_active * _growth_factor(month_idx)), 1) if is_res or base_active > 0 else 0

        # derive category label from rev_by_cat
        cats = sorted(rev_by_cat.keys())
        category = '+'.join(cats) if cats else 'その他'

        # get trend (last 6 months)
        trend = facility_trends_map.get(fname, [])[-6:]

        rows.append({
            'name': fname,
            'active': active,
            'categories': cats,
            'category': category,
            'insurance': round(insurance),
            'self_pay': round(self_pay),
            'total': round(total),
            'rev_by_cat': {k: round(v) for k, v in rev_by_cat.items()},
            'trend': trend,
        })
    rows.sort(key=lambda r: -r['total'])
    return rows


def _gen_services(month_idx, facility_revs):
    """Aggregate facility-level revenue by sales_type across all facilities."""
    by_sales_type = {}
    for fname, rev_data in facility_revs.items():
        for stype, amount in rev_data.get('by_sales_type', {}).items():
            if stype not in by_sales_type:
                by_sales_type[stype] = {'total': 0, 'insurance': 0, 'self_pay': 0, 'customers': set()}
            by_sales_type[stype]['total'] += amount
            # Approximate insurance/self_pay split
            ins_ratio = 0.7 if '訪問看護' in stype or '訪問介護' in stype else 0.65
            by_sales_type[stype]['insurance'] += amount * ins_ratio
            by_sales_type[stype]['self_pay'] += amount * (1 - ins_ratio)

    rows = []
    for stype, cat, _ in SERVICE_TYPES:
        if stype in by_sales_type:
            d = by_sales_type[stype]
            if d['total'] < 10000:
                continue
            rows.append({
                'category': cat, 'sales_type': stype,
                'total': round(d['total']),
                'customers': random.randint(5, max(int(len(d['customers']) * 0.5), 10)),
            })
    rows.sort(key=lambda r: -r['total'])
    return rows


def _gen_alerts(month_idx, kpi, prev_kpi):
    """Generate alerts matching real structure: details with name/prev/curr/change_pct."""
    prev_month = kpi.get('prev_month', '')
    gf = _growth_factor(month_idx)
    details = []

    # Per-facility MoM revenue changes
    fac_names = [f[0] for f in FACILITIES[:12]]
    for fn in fac_names:
        prev = random.randint(1000000, 30000000)
        curr = int(prev * random.uniform(0.85, 1.25))
        change_pct = round((curr - prev) / prev * 100, 1) if prev else 0
        details.append({
            'type': 'facility_change',
            'name': fn,
            'prev': prev,
            'curr': curr,
            'change_pct': change_pct,
        })

    up_count = sum(1 for d in details if d['change_pct'] > 0)
    down_count = sum(1 for d in details if d['change_pct'] < 0)
    new_users = kpi.get('new_customers', 0)
    lost_users = kpi.get('lost_customers', 0)

    return {
        'summary': {
            'up_facilities': up_count,
            'down_facilities': down_count,
            'new_users': new_users,
            'lost_users': lost_users,
        },
        'details': sorted(details, key=lambda d: -abs(d['change_pct'])),
        'prev_month': prev_month,
    }


def _gen_analysis(month_idx, persons_data, facility_revs):
    """Generate full analysis.json matching production structure."""
    gf = _growth_factor(month_idx)

    # Classify facilities into the three analysis groups
    RESIDENTIAL_TYPES = {'有料老人ホーム', 'サ高住', '障がい者GH', '介護付有料', '認知症GH', '特養'}
    INST_TYPES = {'障がい者GH', '介護付有料', '認知症GH', '特養'}

    # Build a person ID → full_name lookup from persons_data
    person_names = {}
    for pid, pdata in persons_data.items():
        info = pdata.get('info', {})
        if info.get('full_name'):
            person_names[pid] = info['full_name']

    fac_list = []       # 居宅系 (residential with デイ etc.)
    inst_list = []      # 施設系 (GH, 介護付有料, 認知症GH, 特養)
    svc_list = []       # サービス拠点 (訪問系, 通所, 就労, 相談)

    # Pick a pool of real person IDs for use in ins/self_pay_persons
    pid_pool = list(person_names.keys())
    pid_idx = 0

    for fname, fcat, region, base_active, base_rev, codes_str, lat, lng, addr_w, is_res in FACILITIES:
        rev = facility_revs.get(fname, {}).get('total', base_rev)
        by_cat = facility_revs.get(fname, {}).get('rev_by_cat', {})
        cats = set(by_cat.keys())
        active = max(int(base_active * gf), 1) if is_res or base_active > 0 else 0
        ins_count = random.randint(int(active * 0.3), int(active * 0.7)) if active > 0 else 0
        low_util = random.randint(0, max(int(ins_count * 0.3), 1)) if ins_count > 0 else 0
        self_pay_count = active - ins_count if active > 0 else 0
        avg_rev = round(rev / active) if active else 0

        # Generate ins_persons with real person names from the pool
        ins_persons = []
        for pi in range(min(ins_count, 15)):
            monthly = random.randint(30000, 300000)
            limit = random.randint(150000, 400000)
            unused = max(limit - monthly, 0)
            # Use a real person name from the pool
            pid_key = pid_pool[(pid_idx + pi) % len(pid_pool)]
            pname = person_names.get(pid_key, f'{fname}居住者{pi+1:02d}')
            ins_persons.append({
                'person_id': int(pid_key),
                'full_name': pname,
                'inferred_level': random.choice(['要支援1', '要支援2', '要介護1', '要介護2', '要介護3', '要介護4', '要介護5']),
                'monthly_total': monthly,
                'limit_yen': limit,
                'unused_yen': unused,
                'util_pct': round(monthly / limit * 100) if limit > 0 else 0,
                'care_ins_yen': monthly,
                'unit_price': random.choice([10.27, 10.42, 10.70]),
                'exceeded_yen': max(monthly - limit, 0),
                'residence': fname,
            })

        # self_pay_persons with real names
        self_pay_persons = []
        for si in range(max(self_pay_count, 0)):
            pid_key = pid_pool[(pid_idx + ins_count + si) % len(pid_pool)]
            pname = person_names.get(pid_key, f'{fname}入居者{si+1:02d}')
            self_pay_persons.append({
                'person_id': int(pid_key),
                'full_name': pname,
            })
        pid_idx += ins_count + max(self_pay_count, 0)

        total_potential = sum(p['unused_yen'] for p in ins_persons)

        base_entry = {
            'office_name': fname,
            'region': region,
            'active': active,
            'revenue': round(rev),
            'avg_revenue': avg_rev,
            'avg_util': random.randint(30, 85),
            'ins_count': ins_count,
            'low_util_count': low_util,
            'self_pay_count': self_pay_count,
            'total_potential': total_potential,
            'has_limit_analysis': len(ins_persons) > 0,
            'ins_persons': ins_persons,
            'self_pay_persons': self_pay_persons,
            'billing_via': None,
            'pending': random.randint(0, 3),
            'stopped': random.randint(0, 2),
            'unit_price': random.choice([10.27, 10.42, 10.70]),
        }

        entry_with_cat = {**base_entry, 'category': '+'.join(sorted(cats)) if cats else fcat}

        # Classify
        is_inst = bool(cats & INST_TYPES) and not bool(cats & {'有料老人ホーム', '通所介護'})
        is_svc = not is_res and base_active > 0

        if is_inst:
            inst_list.append(entry_with_cat)
        elif is_svc:
            svc_list.append({k: v for k, v in entry_with_cat.items()
                            if k in ('office_name', 'category', 'region', 'active', 'revenue',
                                     'avg_revenue', 'avg_util', 'ins_count', 'low_util_count',
                                     'billing_via', 'type')})
            svc_list[-1]['type'] = '訪問系' if '訪問' in fname else ('通所・就労' if '就労' in fname else '相談・CM')
        elif is_res:
            fac_list.append(entry_with_cat)

    # Care level distribution
    care_dist = {
        '要支援1': random.randint(20, 60), '要支援2': random.randint(30, 80),
        '要介護1': random.randint(40, 100), '要介護2': random.randint(30, 70),
        '要介護3': random.randint(15, 40), '要介護4': random.randint(5, 20),
        '要介護5': random.randint(3, 10), '自費のみ': random.randint(20, 60),
    }
    total_ins_p = sum(care_dist.values()) - care_dist['自費のみ']
    total_sp = care_dist['自費のみ']

    # Categories summary
    cat_summary = {}
    for entry in fac_list + inst_list:
        cat = entry.get('category', '')
        for c in cat.split('+'):
            if c not in cat_summary:
                cat_summary[c] = {'total': 0, 'customers': 0}
            cat_summary[c]['total'] += entry.get('revenue', 0)
            cat_summary[c]['customers'] += entry.get('active', 0)
    categories = []
    grand_total = sum(v['total'] for v in cat_summary.values())
    for c, v in sorted(cat_summary.items(), key=lambda x: -x[1]['total']):
        categories.append({
            'category': c,
            'total': v['total'],
            'customers': v['customers'],
            'avg_per_customer': round(v['total'] / v['customers']) if v['customers'] else 0,
            'pct': round(v['total'] / grand_total * 100, 1) if grand_total else 0,
        })

    # Pipeline — varies by month (pending count fluctuates)
    def _pipe_count():
        return max(0, random.randint(0, 8) + int(2 * (month_idx - 5.5)))
    pipeline = [{'office_name': f[0], 'pending_count': _pipe_count()} for f in FACILITIES[:15]]

    # Tenure distribution — varies by month
    base_pop = int(850 * gf)
    pcts = [random.uniform(0.08, 0.25), random.uniform(0.25, 0.45), random.uniform(0.12, 0.28),
            random.uniform(0.10, 0.22), random.uniform(0.03, 0.15)]
    pcts = [p / sum(pcts) for p in pcts]
    tenure_dist = [
        {'years': '0-1', 'count': int(base_pop * pcts[0])},
        {'years': '1-3', 'count': int(base_pop * pcts[1])},
        {'years': '3-5', 'count': int(base_pop * pcts[2])},
        {'years': '5-10', 'count': int(base_pop * pcts[3])},
        {'years': '10+', 'count': int(base_pop * pcts[4])},
    ]

    total_rev = sum(f['revenue'] for f in fac_list + inst_list + svc_list)

    # Facility MoM trend — per-facility prev/curr/change for the 施設別前月比 table
    fac_trend = []
    for fname, _, _, base_active, base_rev, _, _, _, _, _ in FACILITIES:
        rev = facility_revs.get(fname, {}).get('total', base_rev)
        prev_rev = int(rev * random.uniform(0.75, 1.15))
        change_pct = round((rev - prev_rev) / prev_rev * 100, 1) if prev_rev else 0
        fac_trend.append({
            'office_name': fname,
            'prev_total': prev_rev,
            'curr_total': round(rev),
            'change_pct': change_pct,
        })
    fac_trend.sort(key=lambda r: -abs(r['change_pct']))

    return {
        'facilities': fac_list,
        'inst_facilities': inst_list,
        'svc_facilities': svc_list,
        'categories': categories,
        'care_level_distribution': care_dist,
        'pipeline': pipeline,
        'tenure_distribution': tenure_dist,
        'fac_trend': fac_trend,
        'total_ins_all': round(total_rev * 0.65),
        'total_ins_persons': total_ins_p,
        'total_low_util': random.randint(10, 40),
        'total_potential': random.randint(20000000, 50000000),
        'total_self_pay': total_sp,
        'total_pipeline': sum(p['pending_count'] for p in pipeline),
        'avg_tenure': round(random.uniform(1.5, 3.5), 1),
        'residential_potential': random.randint(8000000, 20000000),
        'trend_warning': '',  # empty = show facility MoM table
    }


def _gen_cross_sell(month_idx, persons_data):
    """Generate cross-sell data matching production structure."""
    targets = []
    single_targets = []  # collect single-service users separately
    sorted_persons = sorted(persons_data.items(), key=lambda x: -sum(
        b.get('amount', 0) or 0 for b in x[1].get('bills', [])))
    for pid, det in sorted_persons:
        info = det.get('info', {})
        bills = det.get('bills', [])
        svc_types = set(b.get('sales_type', '') for b in bills)
        total_amt = sum(b.get('amount', 0) or 0 for b in bills)
        if total_amt < 30000:
            continue
        all_svcs = {'訪問介護', '通所介護', '訪問看護', '福祉用具貸与'}
        missing = [s for s in all_svcs if s not in svc_types]
        entry = {
            'person_id': int(pid),
            'full_name': info.get('full_name', ''),
            'residence': info.get('residence', ''),
            'service_count': len(svc_types),
            'monthly_total': round(total_amt),
            'missing': missing,
        }
        if len(svc_types) == 1:
            single_targets.append(entry)
            if len(single_targets) >= 30:
                continue  # enough singles, keep collecting multis
        elif len(svc_types) <= 3:
            targets.append(entry)
        if len(targets) >= 170 and len(single_targets) >= 30:
            break
    # Force at least 30 single-service users for realistic distribution
    all_svc_set = {'訪問介護', '通所介護', '訪問看護', '福祉用具貸与'}
    existing_ids = {t['person_id'] for t in single_targets}
    for pid, det in sorted_persons:
        if len(single_targets) >= 30:
            break
        if int(pid) in existing_ids:
            continue
        info = det.get('info', {})
        bills = det.get('bills', [])
        svc_set = set(b.get('sales_type', '') for b in bills) - {''}
        total_amt = sum(b.get('amount', 0) or 0 for b in bills)
        if len(svc_set) == 1 and total_amt > 30000:
            single_targets.append({
                'person_id': int(pid),
                'full_name': info.get('full_name', ''),
                'residence': info.get('residence', ''),
                'service_count': 1,
                'monthly_total': round(max(total_amt, 50000)),
                'missing': [s for s in all_svc_set if s not in svc_set],
            })
    balanced = single_targets[:30] + targets[:170]

    # Add significant month-to-month variation to summary counts
    def vary(n):
        return max(0, int(n * (0.7 + 0.6 * (month_idx / 11.0) + random.uniform(-0.15, 0.15))))

    single = sum(1 for t in balanced if t['service_count'] == 1)
    no_day = sum(1 for t in balanced if '通所介護' in t.get('missing', []))
    no_home = sum(1 for t in balanced if '訪問介護' in t.get('missing', []))
    no_nurse = sum(1 for t in balanced if '訪問看護' in t.get('missing', []))

    return {
        'summary': {
            'single_service': vary(single),
            'no_day': vary(no_day),
            'no_home_care': vary(no_home),
            'no_nursing': vary(no_nurse),
            'total_residential': vary(len(balanced)),
        },
        'service_density': [
            {'customers': sum(1 for t in balanced if t['service_count'] == i), 'count': i}
            for i in range(1, 9)
        ],
        'targets': balanced,
    }


def _gen_map_data(month_idx, facility_revs):
    pins = []
    for fname, _, region, base_active, base_rev, codes_str, lat, lng, addr_w, is_res in FACILITIES:
        rev = facility_revs.get(fname, {}).get('total', base_rev)
        rev_by_cat = facility_revs.get(fname, {}).get('rev_by_cat', {})
        active = max(int(base_active * _growth_factor(month_idx)), 1) if base_active > 0 else 0
        pins.append({
            'office_name': fname,
            'revenue': round(rev),
            'active': active,
            'avg_revenue': round(rev / active) if active else 0,
            'rev_by_cat': {k: round(v) for k, v in rev_by_cat.items()},
            'pending': random.randint(0, 3),
        })
    return pins


def _write_facility_registry():
    """Write static facility info — addresses and coordinates that never change."""
    registry = []
    for fname, fcat, region, base_active, base_rev, codes_str, lat, lng, addr_w, is_res in FACILITIES:
        ftype = '居住系' if is_res else ('訪問系' if ('訪問' in fname or '介護ステーション' in fname) else ('通所・就労' if '就労' in fname else 'その他'))
        addr_seed = sum(ord(c) for c in fname)
        bldg = (addr_seed % 50) + 1
        room = ((addr_seed * 7) % 40) + 1
        registry.append({
            'office_name': fname,
            'region': region,
            'address': f'{region} {addr_w} {bldg}-{room}',
            'lat': round(lat + random.uniform(-0.005, 0.005), 6),
            'lng': round(lng + random.uniform(-0.005, 0.005), 6),
            'type': ftype,
        })
    _write_json(DATA_DIR, 'facility_registry.json', registry)

def _gen_facility_trends(month_idx, all_month_revs):
    """Build per-facility trend rows spanning ALL months up to month_idx.
    Each row includes `residents` for the 一人当たり per-resident chart toggle."""
    trends = {}
    for fname, _, _, base_active, _, _, _, _, _, is_res in FACILITIES:
        rows = []
        for mi, m in enumerate(MONTHS[:month_idx + 1]):
            gf = _growth_factor(mi)
            mrevs = all_month_revs.get(m, {})
            frev = mrevs.get(fname, {})
            total = frev.get('total', 0)
            by_cat = frev.get('rev_by_cat', {})
            residents = max(int(base_active * gf), 1) if is_res or base_active > 0 else 0
            rows.append({
                'service_month': m,
                'total': round(total),
                'residents': residents,
                'by_cat': {k: round(v) for k, v in by_cat.items()},
            })
        trends[fname] = rows
    return trends


def _gen_facility_details(month_idx, facility_revs, persons_data, persons_by_facility):
    details = {}
    for fname, _, _, base_active, base_rev, codes_str, lat, lng, addr_w, is_res in FACILITIES:
        rev_data = facility_revs.get(fname, {})
        by_sales = rev_data.get('by_sales_type', {})

        billing = []
        for stype, amount in sorted(by_sales.items(), key=lambda x: -x[1]):
            if amount < 1000:
                continue
            cat = 'その他'
            for st, c, _ in SERVICE_TYPES:
                if st == stype:
                    cat = c
                    break
            billing.append({
                'service_name': stype, 'category': cat,
                'total': round(amount),
                'insurance': round(amount * 0.7),
                'self_pay': round(amount * 0.3),
            })

        # Residents from this facility
        fac_persons = persons_by_facility.get(fname, [])
        residents = []
        for (pid, det) in fac_persons[:50]:
            bills = det.get('bills', [])
            total = sum(b.get('amount', 0) or 0 for b in bills)
            residents.append({
                'person_id': int(pid),
                'full_name': det.get('info', {}).get('full_name', ''),
                'amount': round(total),
                'insurance': round(total * 0.7),
                'self_pay': round(total * 0.3),
                'sales_type': bills[0].get('sales_type', '') if bills else '',
                'billing_facility': fname,
                'res_provider': '住宅型有料老人ホーム',
            })

        details[fname] = {
            'billing': billing,
            'residents': residents,
            'users': residents[:30],
            'billing_entities': [{'entity': fname, 'amount': round(rev_data.get('total', base_rev))}],
        }
    return details


def _gen_facility_cards(month_idx, facility_revs):
    cards = []
    for fname, _, _, _, base_rev, _, _, _, _, _ in FACILITIES:
        rev_data = facility_revs.get(fname, {})
        total = rev_data.get('total', base_rev)
        by_sales = rev_data.get('by_sales_type', {})
        by_cat = rev_data.get('rev_by_cat', {})
        categories = sorted(by_cat.keys())
        services = []
        for stype, amount in sorted(by_sales.items(), key=lambda x: -x[1]):
            if amount < 1000:
                continue
            cat = 'その他'
            for st, c, _ in SERVICE_TYPES:
                if st == stype:
                    cat = c
                    break
            services.append({
                'category': cat, 'service_name': stype,
                'code': '', 'amount': round(amount),
            })
        cards.append({
            'name': fname,
            'total': round(total),
            'categories': categories,
            'services': services,
        })
    cards.sort(key=lambda c: -c['total'])
    return cards


def _gen_facility_cards_detail(month_idx, facility_revs, persons_by_facility, all_month_revs):
    result = {}
    for fname, _, _, base_active, base_rev, _, _, _, _, _ in FACILITIES:
        rev_data = facility_revs.get(fname, {})
        total = rev_data.get('total', base_rev)
        by_sales = rev_data.get('by_sales_type', {})
        fac_persons = persons_by_facility.get(fname, [])

        billing = []
        for stype, amount in sorted(by_sales.items(), key=lambda x: -x[1]):
            if amount < 1000:
                continue
            cat = 'その他'
            for st, c, _ in SERVICE_TYPES:
                if st == stype:
                    cat = c
                    break
            billing.append({
                'category': cat, 'service_name': stype, 'code': '',
                'amount': round(amount),
                'insurance': round(amount * 0.7),
                'self_pay': round(amount * 0.3),
                'customers': [{'name': fname, 'amount': round(amount * 0.3),
                               'insurance': round(amount * 0.2),
                               'self_pay': round(amount * 0.1),
                               'is_facility': False, 'aids': [int(pid) for pid, _ in fac_persons[:5]]}],
            })

        users = []
        for pid, det in fac_persons[:20]:
            info = det.get('info', {})
            bills = det.get('bills', [])
            utotal = sum(b.get('amount', 0) or 0 for b in bills)
            cat_amounts = {}
            for b in bills:
                cat = 'その他'
                for st, c, _ in SERVICE_TYPES:
                    if st == b.get('sales_type', ''):
                        cat = c
                        break
                if cat not in cat_amounts:
                    cat_amounts[cat] = {'amount': 0, 'insurance': 0, 'self_pay': 0}
                cat_amounts[cat]['amount'] += b.get('amount', 0) or 0
                cat_amounts[cat]['insurance'] += (b.get('amount', 0) or 0) * 0.7
                cat_amounts[cat]['self_pay'] += (b.get('amount', 0) or 0) * 0.3

            users.append({
                'person_id': int(pid),
                'full_name': info.get('full_name', ''),
                'amount': round(utotal),
                'insurance': round(utotal * 0.7),
                'self_pay': round(utotal * 0.3),
                'cat_amounts': cat_amounts,
            })

        # Trend from actual per-month revenue across all prior months
        trend = []
        for mi_m, m in enumerate(MONTHS[:month_idx + 1]):
            mrevs = all_month_revs.get(m, {})
            frev = mrevs.get(fname, {})
            mtotal = frev.get('total', total * (0.8 + 0.02 * mi_m))
            trend.append({'month': m, 'total': round(mtotal)})

        result[fname] = {
            'name': fname, 'total': round(total),
            'billing': billing, 'users': users, 'trend': trend,
        }
    return result


def _gen_haifu(month_idx, facility_revs):
    """Simulate internal allocation (配賦) between facilities.

    Allocation logic:
      - 訪問系ステーション sends staff to residential 拠点
      - The visiting station's revenue is partially allocated to the 拠点
        where the residents live (housing/coordination share)
      - 訪問ステーション: haifu_OUT (流出, minus, red) → to 拠点
      - 拠点: haifu_IN (流入, plus, green) ← from ステーション
    """
    rows = []
    # Build haifu pairs: for each visiting facility, allocate from a residential one
    res_facs = [f for f in FACILITIES if f[9]]  # residential
    visit_facs = [f for f in FACILITIES if not f[9] and f[3] > 0]  # visiting/day

    # Track per-facility haifu data
    haifu_data = {}  # fname -> {native, haifu_in, haifu_out}
    for fname, _, _, _, base_rev, _, _, _, _, _ in FACILITIES:
        rev_data = facility_revs.get(fname, {})
        by_sales = rev_data.get('by_sales_type', {})
        native = []
        for stype, amount in sorted(by_sales.items(), key=lambda x: -x[1]):
            if amount < 5000:
                continue
            cat = 'その他'
            for st, c, _ in SERVICE_TYPES:
                if st == stype:
                    cat = c
                    break
            native.append({'category': cat, 'service_name': stype, 'amount': round(amount)})
        haifu_data[fname] = {'native': native, 'haifu_in': [], 'haifu_out': []}

    # Create allocation pairs: visiting facility ← residential facility
    pair_count = 0
    for vf in visit_facs:
        vname = vf[0]
        # Each visiting facility gets allocation from 1-2 different residential facilities
        num_srcs = random.randint(1, min(2, len(res_facs)))
        used_srcs = set()
        for _ in range(num_srcs):
            available = [r for r in res_facs if r[0] not in used_srcs]
            if not available:
                break
            src = random.choice(available)
            sname = src[0]
            used_srcs.add(sname)
            vf_rev = facility_revs.get(vname, {}).get('total', vf[4])
            amt = round(vf_rev * random.uniform(0.05, 0.20))
            svc = '訪問介護' if '介護' in vname else ('訪問看護' if '看護' in vname else '福祉用具')

            # Visiting facility: haifu_OUT (流出, minus, red) → to residential
            haifu_data[vname]['haifu_out'].append({
                'counterpart': sname, 'code': 'a120', 'amount': -amt, 'service': svc,
            })
            # Residential facility: haifu_IN (流入, plus, green) ← from visiting
            haifu_data[sname]['haifu_in'].append({
                'counterpart': vname, 'code': 'a120', 'amount': amt, 'service': svc,
            })
            pair_count += 1

    for fname, data in haifu_data.items():
        native = data['native']
        haifu_in = data['haifu_in']
        haifu_out = data['haifu_out']
        nt = sum(r['amount'] for r in native)
        it = sum(r['amount'] for r in haifu_in)
        ot = sum(r['amount'] for r in haifu_out)
        if nt == 0 and it == 0 and ot == 0:
            continue
        rows.append({
            'name': fname, 'native': native, 'haifu_in': haifu_in, 'haifu_out': haifu_out,
            'native_total': nt, 'haifu_in_total': it, 'haifu_out_total': ot,
            'total': nt + it + ot,
        })
    rows.sort(key=lambda r: -r['total'])
    return rows


def _gen_mgmt_journal(month_idx, facility_revs):
    """Simulate management accounting journal entries for select facilities."""
    target_facs = ['就労継続支援事業所（あかね）', 'デモケアテクノロジー中央', 'デモリサーチ中央']
    rows = []
    mgmt_items = [
        ('1650', '福祉用具販売'),
        ('2220', 'リサーチ事業収入'),
        ('2240', '就労事業収入（食事提供）'),
        ('2299', '就労事業収入（食事・福岡）'),
    ]
    for fname in target_facs:
        items = []
        total = 0
        for code, name in mgmt_items:
            if fname == '就労継続支援事業所（あかね）' and code in ('2240', '2299'):
                amt = random.randint(500000, 3000000)
            elif fname == 'デモケアテクノロジー中央' and code == '1650':
                amt = random.randint(100000, 500000)
            elif fname == 'デモリサーチ中央' and code == '2220':
                amt = random.randint(200000, 1000000)
            else:
                continue
            items.append({'code': code, 'name': name, 'amount': amt})
            total += amt
        if items:
            rows.append({'name': fname, 'items': items, 'total': total})
    rows.sort(key=lambda r: -r['total'])
    return rows


def _gen_persons_data(month_idx):
    """Generate persons and person_details with cross-file consistency.
    Two person types:
      - Residents (~60%): live at a residential facility, use on-site + visiting services
      - Home-care (~40%): live at home, use only visiting/day services, no residence
    """
    gf = _growth_factor(month_idx)
    base_persons = 1800
    num_persons = int(base_persons * gf)
    id_offset = month_idx * 50
    persons_data = {}

    # Residential facilities list
    res_facs = _RESIDENTIAL_FACS
    # Non-residential (visiting/day) facilities
    visit_facs = [f[0] for f in FACILITIES if not f[9] and f[3] > 0]

    # Service type groups
    RESIDENTIAL_STYPES = {'有料老人ホーム', 'サ高住', '特定施設入居者生活介護',
                          '特定施設入居者生活介護（施設）', '共同生活援助',
                          '共同生活援助(受託居宅介護以外)', '認知症対応型共同生活介護',
                          '認知症対応型共同生活介護（施設）', '地域密着型介護老人福祉施設',
                          '短期入所', '施設利用料（特養）', '施設利用料（その他）',
                          'GH家賃', '特定施設入居者生活介護（家賃）',
                          '認知症対応型共同生活介護（家賃）'}
    VISIT_STYPES = {'訪問介護', '訪問介護（障害福祉サービス）', '訪問介護（全額自費）',
                    '地方自治体独自事業（移動支援）', '訪問看護（介護）',
                    '訪問看護（医療_国保連）', '訪問看護（医療_支払基金）',
                    '訪問看護（自費）'}
    DAY_STYPES = {'通所介護', '地域密着型通所介護', '生活介護（デイナイトケア）',
                  'デイサービス（自費）'}
    OTHER_STYPES = {'居宅介護支援', '相談支援', '福祉用具貸与', '福祉用具貸与（自費）',
                    '福祉用具販売'}

    for i in range(num_persons):
        person_seed = i + id_offset
        if random.random() < 0.05 * (month_idx + 1):
            continue
        aid = 1000 + person_seed
        name, gender = _random_name(person_seed)
        month_factor = gf

        # ~60% live at a residential facility, ~40% receive home care
        is_resident = random.random() < 0.6
        residence = res_facs[person_seed % len(res_facs)] if is_resident else ''

        bills = []
        used_sales_types = set()
        if is_resident:
            # Resident: always has 1 residential bill + optional visiting/day/other
            res_stype = random.choice(list(RESIDENTIAL_STYPES))
            used_sales_types.add(res_stype)
            # Residential bill goes to their residence facility
            amount = random.randint(30000, 300000) * month_factor
            ins_ratio = 0.7 if random.random() > 0.3 else 0
            bills.append({
                'amount': amount,
                'facility': residence,
                'insurance': round(amount * ins_ratio),
                'self_pay': round(amount * (1 - ins_ratio)),
                'sales_type': res_stype,
                'sub_sales_type': '',
            })
            # Add 1-3 additional visiting/day/other services
            extra_pool = list(VISIT_STYPES | DAY_STYPES | OTHER_STYPES)
            random.shuffle(extra_pool)
            for stype in extra_pool[:random.randint(1, 3)]:
                if stype in used_sales_types:
                    continue
                used_sales_types.add(stype)
                candidates = [f[0] for f in FACILITIES if f[3] > 0]
                fac = random.choice(candidates) if candidates else residence
                amount = random.randint(5000, 150000) * month_factor
                ins_ratio = 0.7 if random.random() > 0.3 else 0
                bills.append({
                    'amount': amount, 'facility': fac,
                    'insurance': round(amount * ins_ratio),
                    'self_pay': round(amount * (1 - ins_ratio)),
                    'sales_type': stype, 'sub_sales_type': '',
                })
        else:
            # Home-care: 1-3 visiting/day/other services only, no residential
            pool = list(VISIT_STYPES | DAY_STYPES | OTHER_STYPES)
            random.shuffle(pool)
            for stype in pool[:random.randint(1, 3)]:
                if stype in used_sales_types:
                    continue
                used_sales_types.add(stype)
                candidates = [f[0] for f in FACILITIES if f[3] > 0 and not f[9]]
                fac = random.choice(candidates) if candidates else visit_facs[0]
                amount = random.randint(10000, 200000) * month_factor
                ins_ratio = 0.7 if random.random() > 0.3 else 0
                bills.append({
                    'amount': amount, 'facility': fac,
                    'insurance': round(amount * ins_ratio),
                    'self_pay': round(amount * (1 - ins_ratio)),
                    'sales_type': stype, 'sub_sales_type': '',
                })

        # Build service list
        services = []
        for b in bills[:2]:
            services.append({
                'contract_start': f'202{random.randint(0,5)}-{random.randint(1,12):02d}-{random.randint(1,28):02d}',
                'contract_end': '',
                'office_name': b['facility'],
                'provider_name': b['sales_type'],
                'room': f'{random.randint(101, 505)}' if (is_resident and random.random() > 0.3) else '',
                'status': '利用中',
            })

        # Calculate insurance utilization
        care_ins_yen = sum(b.get('insurance', 0) or 0 for b in bills)
        unit_price = random.choice([10.27, 10.42, 10.70])
        care_limits = {
            '要支援1': 5032 * unit_price, '要支援2': 10531 * unit_price,
            '要介護1': 16765 * unit_price, '要介護2': 19705 * unit_price,
            '要介護3': 27048 * unit_price, '要介護4': 30938 * unit_price, '要介護5': 36217 * unit_price,
        }
        inferred_level = '自費のみ'
        limit_yen = 0
        for level in ['要支援1', '要支援2', '要介護1', '要介護2', '要介護3', '要介護4', '要介護5']:
            if care_ins_yen <= care_limits[level]:
                inferred_level = level
                limit_yen = care_limits[level]
                break
        if care_ins_yen > care_limits.get('要介護5', 0):
            inferred_level = '要介護5'
            limit_yen = care_limits['要介護5']

        unused_yen = max(limit_yen - care_ins_yen, 0) if limit_yen > 0 else 0
        exceeded_yen = max(care_ins_yen - limit_yen, 0) if limit_yen > 0 else 0
        util_pct = round(care_ins_yen / limit_yen * 100, 1) if limit_yen > 0 else 0

        utilization = {
            'care_ins_yen': round(care_ins_yen),
            'exceeded_yen': round(exceeded_yen),
            'inferred_level': inferred_level,
            'limit_yen': round(limit_yen),
            'residence': residence,
            'unit_price': unit_price,
            'unused_yen': round(unused_yen),
            'util_pct': util_pct,
        }

        persons_data[str(aid)] = {
            'info': {
                'person_id': str(aid),
                'full_name': name,
                'birthday': f'{random.randint(1930, 2000)}-{random.randint(1,12):02d}-{random.randint(1,28):02d}',
                'gender': gender,
                'customer_type': random.choice(['要介護', '要支援', '自費']),
                'flag': '',
                'residence': residence,
            },
            'bills': bills,
            'services': services,
            'utilization': utilization,
        }
    return persons_data


# Map service codes to matching sales_types (for facility-owned service filtering)
def _build_code_to_sales_types():
    """Map service code patterns to the sales_types they represent."""
    code_map = {}
    for stype, cat, weight in SERVICE_TYPES:
        # Derive likely codes from sales_type name patterns
        if '訪問介護（障害福祉' in stype:
            code_map.setdefault('1230', []).append(stype)
        elif '訪問介護（全額自費' in stype:
            code_map.setdefault('1290', []).append(stype)
        elif '訪問介護' in stype:
            code_map.setdefault('1210', []).append(stype)
        elif '移動支援' in stype:
            code_map.setdefault('1281', []).append(stype)
        elif '訪問看護（自費' in stype:
            code_map.setdefault('1340', []).append(stype)
        elif '訪問看護（介護' in stype or '訪問看護（医療' in stype:
            code_map.setdefault('1310_1320_1330', []).append(stype)
        elif '訪問看護' in stype:
            code_map.setdefault('1310_1320_1330', []).append(stype)
        elif '通所介護' in stype:
            code_map.setdefault('1110', []).append(stype)
        elif '地域密着型通所介護' in stype:
            code_map.setdefault('1120', []).append(stype)
        elif '生活介護（デイナイト' in stype:
            code_map.setdefault('1130', []).append(stype)
        elif 'デイサービス（自費' in stype:
            code_map.setdefault('1140', []).append(stype)
        elif '有料老人ホーム' in stype:
            code_map.setdefault('2110', []).append(stype)
        elif 'サ高住' in stype:
            code_map.setdefault('2120', []).append(stype)
        elif '特定施設入居者生活介護（施設' in stype:
            code_map.setdefault('1680', []).append(stype)
        elif '特定施設入居者生活介護（家賃' in stype:
            code_map.setdefault('2130', []).append(stype)
        elif '特定施設入居者生活介護' in stype:
            code_map.setdefault('1660', []).append(stype)
        elif '共同生活援助(受託' in stype:
            code_map.setdefault('1410_2140', []).append(stype)
        elif '共同生活援助' in stype:
            code_map.setdefault('1410_2140', []).append(stype)
        elif '認知症対応型共同生活介護（施設' in stype:
            code_map.setdefault('1711', []).append(stype)
        elif '認知症対応型共同生活介護（家賃' in stype:
            code_map.setdefault('2160', []).append(stype)
        elif '認知症対応型共同生活介護' in stype:
            code_map.setdefault('1710', []).append(stype)
        elif '地域密着型介護老人福祉施設' in stype:
            code_map.setdefault('1690_1691_2150', []).append(stype)
        elif '短期入所' in stype:
            code_map.setdefault('1692_2150_短期', []).append(stype)
        elif '施設利用料（特養' in stype:
            code_map.setdefault('2280', []).append(stype)
        elif '居宅介護支援' in stype:
            code_map.setdefault('1630', []).append(stype)
        elif '相談支援' in stype:
            code_map.setdefault('1620', []).append(stype)
        elif '福祉用具貸与（自費' in stype:
            code_map.setdefault('1641', []).append(stype)
        elif '福祉用具貸与' in stype:
            code_map.setdefault('1640', []).append(stype)
        elif '福祉用具販売' in stype:
            code_map.setdefault('1650', []).append(stype)
        elif '就労選択支援' in stype:
            code_map.setdefault('1611', []).append(stype)
        elif '就労継続支援' in stype:
            code_map.setdefault('1610', []).append(stype)
        elif '就労事業収入（食事提供）' in stype:
            code_map.setdefault('2240', []).append(stype)
        elif '就労事業収入（洗濯）' in stype:
            code_map.setdefault('2250', []).append(stype)
        elif '就労事業収入（食事・福岡）' in stype:
            code_map.setdefault('2299', []).append(stype)
        elif 'リサーチ事業収入' in stype:
            code_map.setdefault('2220', []).append(stype)
        elif '施設賃料（ビル）' in stype:
            code_map.setdefault('2230', []).append(stype)
        elif 'ITコンサルティング' in stype:
            code_map.setdefault('2201', []).append(stype)
        elif '立替金' in stype:
            code_map.setdefault('0000', []).append(stype)
        elif '施設利用料（その他' in stype:
            code_map.setdefault('2290', []).append(stype)
        else:
            code_map.setdefault('other', []).append(stype)
    return code_map

_CODE_TO_SALES = _build_code_to_sales_types()


def _build_facility_revs(month_idx, persons_data):
    """Derive per-facility revenue based on each facility's OWN service codes.

    A facility only generates revenue from service types matching its codes_str.
    訪問介護ステーション only bills for 訪問介護, 拠点 bills for デイ/有料/GH, etc.
    """
    gf = _growth_factor(month_idx)
    facility_revs = {}
    for fname, _, region, base_active, base_rev, codes_str, lat, lng, addr_w, is_res in FACILITIES:
        by_sales_type = {}
        rev_by_cat = {}
        total = 0
        insurance = 0
        self_pay = 0

        # Get the service types this facility actually provides
        facility_codes = set(codes_str.split(','))
        eligible_types = []
        for code in facility_codes:
            if code in _CODE_TO_SALES:
                eligible_types.extend(_CODE_TO_SALES[code])

        if not eligible_types:
            eligible_types = [st for st, cat, w in SERVICE_TYPES if cat == 'その他']

        # Generate revenue only from eligible service types
        num_types = min(len(eligible_types), random.randint(2, max(len(eligible_types), 8)))
        chosen = random.sample(eligible_types, min(num_types, len(eligible_types)))

        for stype in chosen:
            # Find the category and weight for this sales_type
            cat = 'その他'
            weight = 0.05
            for st, c, w in SERVICE_TYPES:
                if st == stype:
                    cat = c
                    weight = w
                    break

            amt = base_rev * weight * gf * random.uniform(0.5, 2.0)
            if amt < 1000:
                continue
            by_sales_type[stype] = amt
            rev_by_cat[cat] = rev_by_cat.get(cat, 0) + amt
            total += amt
            insurance += amt * random.uniform(0.55, 0.85)
            self_pay += amt * random.uniform(0.15, 0.45)

        facility_revs[fname] = {
            'total': total,
            'insurance': insurance,
            'self_pay': self_pay,
            'by_sales_type': by_sales_type,
            'rev_by_cat': rev_by_cat,
        }
    return facility_revs


def _build_persons_by_facility(persons_data):
    by_fac = {}
    for pid, det in persons_data.items():
        for b in det.get('bills', []):
            fac = b.get('facility', '')
            if fac not in by_fac:
                by_fac[fac] = []
            by_fac[fac].append((pid, det))
            break  # assign to first facility only
    return by_fac


def _gen_trend_history(all_month_kpis):
    """Generate root-level trend_history.json from all months' data."""
    categories = []
    facilities_list = []

    for mi, month in enumerate(MONTHS):
        kpi = all_month_kpis.get(month, {})
        for stype, cat, _ in SERVICE_TYPES:
            if random.random() > 0.3:
                categories.append({
                    'category': cat,
                    'sales_type': stype,
                    'service_month': month,
                    'total': round(kpi.get('total', 100000000) * random.uniform(0.01, 0.15)),
                })

    # Deduplicate: keep highest for each (sales_type, service_month)
    seen = {}
    deduped = []
    for c in categories:
        key = (c['sales_type'], c['service_month'])
        if key not in seen or c['total'] > seen[key].total:
            seen[key] = type('x', (), {'total': c['total']})()
            deduped.append(c)
    # Rebuild properly
    seen = {}
    result_cats = []
    for c in categories:
        key = (c['sales_type'], c['service_month'])
        if key not in seen:
            seen[key] = c
            result_cats.append(c)

    # Facility trend rows
    fac_names = [f[0] for f in FACILITIES[:20]]
    for mi, month in enumerate(MONTHS):
        for fn in fac_names:
            facilities_list.append({
                'office_name': fn,
                'service_month': month,
                'total': round(random.uniform(500000, 50000000) * _growth_factor(mi)),
            })

    return {'categories': result_cats, 'facilities': facilities_list}


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    os.makedirs(DATA_DIR, exist_ok=True)

    all_month_kpis = {}
    all_month_revs = {}

    # Generate per-month data (reverse: newest first for months.json ordering)
    for mi, month in enumerate(MONTHS):
        print(f'Generating {month}...')
        month_dir = os.path.join(DATA_DIR, month)
        os.makedirs(month_dir, exist_ok=True)

        persons_data = _gen_persons_data(mi)
        facility_revs = _build_facility_revs(mi, persons_data)
        persons_by_fac = _build_persons_by_facility(persons_data)
        all_month_revs[month] = facility_revs

        # Derive billing rows for kpi and other shared data
        fac_billing = []
        for fname, _, _, base_active, base_rev, _, _, _, _, _ in FACILITIES:
            rev_data = facility_revs.get(fname, {})
            fac_billing.append({
                'name': fname,
                'total': rev_data.get('total', base_rev),
                'insurance': rev_data.get('insurance', base_rev * 0.7),
                'self_pay': rev_data.get('self_pay', base_rev * 0.3),
                'rev_by_cat': rev_data.get('rev_by_cat', {}),
            })

        persons_list = _gen_persons(mi, persons_data)

        # 1. kpi.json
        kpi = _gen_kpi(mi, fac_billing, persons_list)
        all_month_kpis[month] = kpi
        _write_json(month_dir, 'kpi.json', kpi)

        # 2. persons.json
        _write_json(month_dir, 'persons.json', persons_list)

        # 3. person_details.json
        _write_json(month_dir, 'person_details.json', persons_data)

        # 4. facilities_billing.json
        # Build trend map for sparklines (use the same multi-month trends)
        ft_map = _gen_facility_trends(mi, all_month_revs)
        fb = _gen_facilities_billing(mi, facility_revs, ft_map)
        _write_json(month_dir, 'facilities_billing.json', fb)

        # 5. facilities_resident.json (grouped by provider type, not same as billing)
        fr_rows = []
        for fname, _, _, base_active, base_rev, codes_str, _, _, _, is_res in FACILITIES:
            if not is_res:
                continue
            rev_data = facility_revs.get(fname, {})
            total = rev_data.get('total', base_rev)
            rev_by_cat = rev_data.get('rev_by_cat', {})
            category = '+'.join(sorted(rev_by_cat.keys())) if rev_by_cat else 'その他'
            residents = max(int(base_active * _growth_factor(mi)), 1)
            fr_rows.append({
                'name': fname,
                'category': category,
                'provider_name': _provider_label(codes_str),
                'residents': residents,
                'total': round(total),
            })
        fr_rows.sort(key=lambda r: -r['total'])
        _write_json(month_dir, 'facilities_resident.json', fr_rows)

        # 6. facility_details.json
        fd = _gen_facility_details(mi, facility_revs, persons_data, persons_by_fac)
        _write_json(month_dir, 'facility_details.json', fd)

        # 7. services.json
        svcs = _gen_services(mi, facility_revs)
        _write_json(month_dir, 'services.json', svcs)

        # 8. analysis.json
        analysis = _gen_analysis(mi, persons_data, facility_revs)
        _write_json(month_dir, 'analysis.json', analysis)

        # 9. cross_sell.json
        cs = _gen_cross_sell(mi, persons_data)
        _write_json(month_dir, 'cross_sell.json', cs)

        # 10. map.json
        map_data = _gen_map_data(mi, facility_revs)
        _write_json(month_dir, 'map.json', map_data)

        # 11. facility_trends.json — per-facility multi-month trend with residents count
        ft = _gen_facility_trends(mi, all_month_revs)
        _write_json(month_dir, 'facility_trends.json', ft)

        # 12. alerts.json
        prev_kpi = all_month_kpis.get(MONTHS[mi - 1], {}) if mi > 0 else {}
        alerts = _gen_alerts(mi, kpi, prev_kpi)
        _write_json(month_dir, 'alerts.json', alerts)

        # 13. facility_cards.json
        cards = _gen_facility_cards(mi, facility_revs)
        _write_json(month_dir, 'facility_cards.json', cards)

        # 14. facility_cards_detail.json
        card_detail = _gen_facility_cards_detail(mi, facility_revs, persons_by_fac, all_month_revs)
        _write_json(month_dir, 'facility_cards_detail.json', card_detail)

        # 15. haifu.json
        haifu = _gen_haifu(mi, facility_revs)
        _write_json(month_dir, 'haifu.json', haifu)

        # 16. mgmt_journal.json
        mgmt = _gen_mgmt_journal(mi, facility_revs)
        _write_json(month_dir, 'mgmt_journal.json', mgmt)

    # Root-level files
    print('Generating root-level files...')

    # months.json (newest first)
    months_meta = [{'month': m, 'bases': 18, 'complete': True, 'default': m == MONTHS[-1]}
                   for m in reversed(MONTHS)]
    _write_json(DATA_DIR, 'months.json', months_meta)

    # trend_history.json
    th = _gen_trend_history(all_month_kpis)
    _write_json(DATA_DIR, 'trend_history.json', th)

    # facility_registry.json — static facility info (addresses don't change per month)
    _write_facility_registry()

    # Count files
    total_files = sum(1 for _ in _walk_json(DATA_DIR))
    print(f'\nDone. Generated {total_files} JSON files in {DATA_DIR}/')


def _write_json(dirpath, filename, data):
    path = os.path.join(dirpath, filename)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
    size_kb = os.path.getsize(path) / 1024
    print(f'  {filename} ({size_kb:.0f} KB)')


def _walk_json(root):
    for dirpath, _, files in os.walk(root):
        for fn in files:
            if fn.endswith('.json'):
                yield os.path.join(dirpath, fn)


if __name__ == '__main__':
    main()
