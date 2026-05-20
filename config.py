"""Shared constants and pure functions for sales dashboard."""

# ── Load AppSheet data if available (replaces hardcoded mappings for new facilities) ──
_appsheet_cache = None
try:
    import appsheet_sync
    _appsheet_cache = appsheet_sync.load_cache()
    if _appsheet_cache:
        _appsheet_mappings = appsheet_sync.build_mappings(_appsheet_cache)
    else:
        _appsheet_mappings = None
except Exception:
    _appsheet_mappings = None

# Provider types that indicate where a customer LIVES (residential)
# Updated from AppSheet if available, otherwise hardcoded fallback
RESIDENTIAL = _appsheet_mappings['residential'] if _appsheet_mappings else (
    '共同生活援助',
    '住宅型有料老人ホーム',
    '介護付き有料老人ホーム',
    '認知症対応型共同生活介護',
    '地域密着型特別養護老人ホーム',
    'サービス付き高齢者向け住宅',
)

# sales_type substrings that indicate residency at service_office (sales_summary
# side). Matching done via NFKC-normalized substring check to tolerate
# full/half-width punctuation and whitespace drift in upstream data.
RESIDENTIAL_SALES_PATTERNS = (
    '有料老人ホーム',          # covers 住宅型/介護付 variants
    'サ高住',
    'サービス付き高齢者向け住宅',
    '特定施設入居者生活介護',
    '共同生活援助',            # includes 共同生活援助(受託居宅介護以外) etc.
    '認知症対応型共同生活介護',
    '地域密着型介護老人福祉施設',
    '特養',
    '短期入所',
)

def _nfkc(s):
    """NFKC-normalize and strip whitespace. Safe against None."""
    if not s:
        return ''
    import unicodedata
    return unicodedata.normalize('NFKC', s).replace(' ', '').replace('　', '')

def is_residential_sales_type(sales_type):
    """True if sales_type indicates residency (for is_residential flag)."""
    n = _nfkc(sales_type)
    if not n:
        return False
    return any(p in n for p in (_nfkc(p) for p in RESIDENTIAL_SALES_PATTERNS))

# Mapping from billing base_name to office_name(s) in customer_service_map.
# Only needed where the names don't naturally match via substring.
BASE_TO_OFFICE = {
    '0037_ひまわり園':   ['ひまわり'],
    '0043_つばき苑':    ['つばき'],
    '0048_さくら拠点': ['さくら1', 'さくら2'],
    '0044_みどり拠点':  ['みどり1'],
}

# For base_names that share billing across multiple facilities, route each bill
# to the correct facility by sales_type keyword match.
BASE_SALES_TYPE_ROUTE = {
    '0048_さくら拠点': [
        ('特定施設', 'さくら2'),
        ('認知症',   'さくら1'),
    ],
}

# Offices whose billing goes through another office's billing entity.
BILLING_VIA = {
    'さくら2':       ('0048_さくら拠点', 'さくら1'),
    'あやめ':         ('0039_訪問看護事業所（中央2）', '中央_訪問看護2'),
    '訪問介護北':      ('0011_訪問介護事業所（中央）', '中央'),
    '訪問介護南':      ('0011_訪問介護事業所（中央）', '中央'),
    'みどり2':        ('0015_訪問介護事業所（東）', '東_訪問介護'),
}


# ── base_name → (display, area) resolution (Phase D) ─────────────────────
# Rule: strip the leading 4-digit prefix (NNNN_) from base_name; use the
# remainder as the display name. Every distinct base_name becomes its own
# 拠点別請求 row, labelled with the text the billing system itself uses.
# No curation dict required — new base_names flow through automatically.
#
#   '0011_訪問介護事業所（中央）'     → '訪問介護事業所（中央）'     area='中央'
#   '0012_就労継続支援事業所（あかね）' → '就労継続支援事業所（あかね）' area='あかね'
#   '0054_訪問介護事業所北'        → '訪問介護事業所北'        area='北'
#   '0005_桜木拠点'               → '桜木拠点'               area='桜木'
#   '0016_障がい者GH すみれ'       → '障がい者GH すみれ'       area='すみれ'
#   'けやきビル'                   → 'けやきビル'              area='けやきビル'
#
# `area` is used by /api/map to aggregate split offices back to one physical
# pin (e.g. three 東 base_names all share area='東'). It's a heuristic
# extraction — falls back to the display name itself when nothing obvious
# can be pulled.
import re as _re

# Boss's rename list (2026-04-23). Applied after strip-NNNN_prefix so keys are
# the stripped base_name form. Area derivation runs on the ORIGINAL stripped
# name so map-pin grouping stays stable (e.g. '訪問介護事業所（東）' renamed
# to 'デモ介護ステーション東' still groups under area='東').
# Demo rename list — generic facility display names replacing originals.
# Applied after strip-NNNN_prefix so keys are the stripped base_name form.
_NAME_OVERRIDES = {
    '訪問介護事業所（桜木）':       'デモ介護ステーション中央',
    '訪問介護事業所北':             'デモ介護ステーション北',
    '訪問介護事業所南':             'デモ介護ステーション南',
    '訪問介護事業所（東）':         'デモ介護ステーション東',

    '訪問看護事業所（中央）':       'デモ訪問看護ステーション中央',
    '訪問看護事業所（東）':         'デモ訪問看護ステーション東',
    '訪問看護事業所（中央2）':      'デモ訪問看護ステーション南',
    '訪問看護事業所（西）':         'デモ訪問看護ステーション西',

    'デモケアテクノロジー（中央':   'デモケアテクノロジー（中央）',
    'デモケアテクノロジー（東':     'デモケアテクノロジー（東）',

    '居宅介護支援事業所':           '居宅介護支援事業所（中央）',
    '居宅介護支援事業所（東）':     '居宅介護支援事業所（東）',

    'さくら1':                     'グループホームさくら',
    'さくら2':                     '介護付有料老人ホームさくら',

    'ひまわり園':                  'ひまわり拠点',
    'つばき苑':                    'つばき拠点',
}

def display_for_base_name(bn):
    """Return (display_name, area) for a sales_summary.base_name."""
    if not bn:
        return bn, bn
    m = _re.match(r'^(\d{4})_(.+)$', bn)
    stripped = m.group(2) if m else bn          # strip NNNN_ prefix when present
    display = _NAME_OVERRIDES.get(stripped, stripped)
    # Derive area from the ORIGINAL stripped name — overrides change the
    # displayed label but not the underlying location identity.
    m2 = _re.search(r'（([^）]+)）', stripped)        # (エリア) — full-width parens
    if m2:
        area = m2.group(1)
    elif (m3 := _re.match(r'^(.+)拠点$', stripped)):       # ～拠点
        area = m3.group(1)
    elif (m4 := _re.match(r'^(?:障がい者GH|認知症GH)\s*(.+)$', stripped)):
        area = m4.group(1)                               # "障がい者GH 宇宿" → "宇宿"
    elif (m5 := _re.match(r'^.+?事業所(.+)$', stripped)):  # "訪問介護事業所鹿児島北" → "鹿児島北"
        area = m5.group(1)
    else:
        area = stripped                                  # fallback: area = stripped name
    return display, area


# Legacy alias — snapshot.py / server_snapshot.py used to import this dict
# directly. Phase D keeps the name but populates it lazily (empty dict, since
# every base_name now flows through display_for_base_name above). Any code
# still iterating over it will simply get no entries; callers should migrate
# to display_for_base_name(bn) for per-row resolution.
BASE_NAME_TO_OFFICE = {}

# ── Haifu (allocation) view mappings ────────────────────────────────────────
# Maps the allocation matrix column headers to the canonical
# display-facility names used elsewhere in the dashboard (output of
# display_for_base_name + _NAME_OVERRIDES). Short labels like
# '桜木' / '訪問介護（中央）' map to '桜木拠点' / 'デモ介護ステーション中央'.
HAIFU_DESTINATION_TO_OFFICE = {
    '桜木':     '桜木拠点',
    'けやき':   'けやき拠点',
    'つつじ':   'つつじ拠点',
    '中央':     '中央拠点',
    'みなと':   'みなと拠点',
    'あかね':   'あかね拠点',
    'ひがし':   'ひがし拠点',
    'すみれ':   'すみれ拠点',
    '若葉の里': '若葉の里',
    'みどり':   'みどり拠点',
    'ゆうひ':   'ゆうひ拠点',
    'こまくさ': 'こまくさ拠点',
    'しらかば': 'しらかば拠点',
}
HAIFU_SOURCE_TO_OFFICE = {
    '訪問介護（東）':   'デモ介護ステーション東',
    '訪問看護（東）':   'デモ訪問看護ステーション東',
    '訪問介護（中央）': 'デモ介護ステーション中央',
    '訪問看護（中央）': 'デモ訪問看護ステーション中央',
    '訪問看護（南）':   'デモ訪問看護ステーション南',
    '訪問看護（西）':   'デモ訪問看護ステーション西',
    '訪問介護（北）':   'デモ介護ステーション北',
    '訪問介護（南）':   'デモ介護ステーション南',
}

# Provider name → sales_type keywords for attributing revenue to service offices.
# Used to calculate actual income when billing goes through another entity.
PROVIDER_SALES_MATCH = {
    '訪問介護':             ['訪問介護'],
    '訪問看護':             ['訪問看護'],
    '通所介護':             ['通所介護', '地域密着型通所介護', 'デイ', '生活介護'],
    '福祉用具':             ['福祉用具'],
    '居宅介護支援':           ['居宅介護支援'],
    '相談支援':             ['相談支援'],
    '共同生活援助':           ['共同生活援助'],
    '介護付き有料老人ホーム':    ['特定施設'],
    '認知症対応型共同生活介護':   ['認知症対応型'],
    '地域密着型特別養護老人ホーム': ['地域密着型介護老人', '短期入所'],
    '住宅型有料老人ホーム':      ['有料老人ホーム'],
    'サービス付き高齢者向け住宅':  ['サ高住'],
    '就労継続支援B型':         ['就労'],
    '就労選択支援':           ['就労選択'],
    'ITコンサルティング事業':    ['IT'],
    '短期入所生活介護':         ['短期入所'],
}

SERVICE_CODE_NAMES = {
    '0000':               '立替金',
    '1110':               '通所介護（デイサービス）',
    '1120':               '地域密着型通所介護',
    '1130':               '生活介護（デイナイトケア）',
    '1140':               'デイサービス（自費）',
    '1210':               '訪問介護',
    '1230':               '訪問介護（障害福祉サービス）',
    '1270':               '鹿児島市負担額（訪問介護）',
    '1281':               '移動支援',
    '1290':               '訪問介護（全額自費）',
    '1310_1320_1330':     '訪問看護',
    '1340':               '訪問看護（自費）',
    '1350':               '鹿児島市負担額（訪問看護）',
    '1410_2140':          'GH家賃',
    '1610':               '就労継続支援（自己負担）',
    '1611':               '就労選択支援',
    '1620':               '相談支援',
    '1630':               '居宅介護支援',
    '1630_居宅予防':      '居宅介護支援（予防）',
    '1640':               '福祉用具貸与',
    '1641':               '福祉用具貸与（自費）',
    '1650':               '福祉用具販売',
    '1660':               '特定施設入居者生活介護',
    '1680':               '特定施設入居者生活介護（自費）',
    '1690_1691_2150':     '地域密着型特別養護老人ホーム',
    '1692_2150_短期':     '短期入所',
    '1710':               '認知症対応型共同生活介護',
    '1711':               '認知症対応型共同生活介護（自費）',
    '2110':               '有料老人ホーム',
    '2120':               'サービス付き高齢者向け住宅',
    '2130':               '特定施設入居者生活介護（家賃）',
    '2160':               '認知症対応型共同生活介護（家賃）',
    '2201':               'ITコンサルティング事業',
    '2220':               'リサーチ事業収入',
    '2230':               '施設賃料（ビル）',
    '2240':               '就労事業収入（食事提供）',
    '2242_2243_2245_2299':'就労事業収入（その他）',
    '2250':               '就労事業収入（洗濯）',
    '2280':               '施設利用料（特養）',
    '2290':               '施設利用料（その他）',
    '2299':               '就労事業収入（食事・福岡）',
}


# ── Category definitions (aligned with 5 business divisions) ──────────────────
#
# Divisions: 訪問系 / 通所系（デイ）/ 居住系 / 相談・貸与 / その他事業
# 拠点 = 住宅型有料老人ホーム + デイサービス combined hub
# 障がい者GH and 認知症GH are distinct residential types
#
_PROVIDER_CATEGORY_HARDCODED = {
    '共同生活援助':                 '障がい者GH',
    '住宅型有料老人ホーム':          '有料老人ホーム',
    '介護付き有料老人ホーム':        '介護付有料',
    '認知症対応型共同生活介護':      '認知症GH',
    '地域密着型特別養護老人ホーム':  '特養',
    'サービス付き高齢者向け住宅':    '有料老人ホーム',
    '訪問介護':                     '訪問介護',
    '訪問看護':                     '訪問看護',
    '通所介護':                     '通所介護',
    '福祉用具':                     '福祉用具',
    '居宅介護支援':                 '相談・CM',
    '相談支援':                     '相談・CM',
    '就労継続支援B型':              '就労支援',
    '就労選択支援':                 '就労選択',
    '短期入所生活介護':             '特養',
    'ITコンサルティング事業':       'その他',
    '介護施設紹介':                 'その他',
    '洗濯代行':                     'その他',
}
# Merge: AppSheet provider_category takes precedence, hardcoded as fallback
if _appsheet_mappings:
    PROVIDER_CATEGORY = {**_PROVIDER_CATEGORY_HARDCODED, **_appsheet_mappings['provider_category']}
else:
    PROVIDER_CATEGORY = _PROVIDER_CATEGORY_HARDCODED

SERVICE_CODE_CATEGORY = {
    # ── 通所系（デイサービス / 小規模デイ）──
    '1110': '通所介護',      # 通所介護（デイサービス）
    '1120': '通所介護',      # 地域密着型通所介護（小規模デイ）
    '1130': '通所介護',      # 生活介護（デイナイトケア）
    '1140': '通所介護',      # デイサービス（自費）
    # ── 訪問系 ──
    '1210': '訪問介護',
    '1230': '訪問介護',              # 訪問介護（障害福祉・ホームヘルプ）
    '1270': '訪問介護',              # 鹿児島市負担額（訪問介護）
    '1281': '訪問介護',              # 移動支援（訪問系に含む）
    '1290': '訪問介護',              # 訪問介護（全額自費）
    '1310_1320_1330': '訪問看護',
    '1340': '訪問看護',              # 訪問看護（自費）
    '1350': '訪問看護',              # 鹿児島市負担額（訪問看護）
    # ── 障がい者GH ──
    '1410_2140': '障がい者GH',       # 共同生活援助（障がい者グループホーム家賃）
    # ── 就労支援 ──
    '1610': '就労支援',              # 就労継続支援B型（就B）
    '1611': '就労選択',              # 就労選択支援
    '2240': '就労支援',              # 就労事業収入（食事提供）
    '2242_2243_2245_2299': '就労支援',
    '2250': '就労支援',              # 就労事業収入（洗濯）
    '2299': '就労支援',              # 就労事業収入（食事・福岡）
    # ── 相談・CM ──
    '1620': '相談・CM',              # 相談支援（障害福祉）
    '1630': '相談・CM',              # 居宅介護支援（ケアマネ）
    '1630_居宅予防': '相談・CM',
    # ── 福祉用具（レンタル・販売）──
    '1640': '福祉用具',
    '1641': '福祉用具',
    '1650': '福祉用具',
    # ── 有料老人ホーム（住宅型・サ高住 — 拠点の居室費）──
    '2110': '有料老人ホーム',        # 住宅型有料老人ホーム
    '2120': '有料老人ホーム',        # サービス付き高齢者向け住宅（サ高住）
    '2290': '有料老人ホーム',        # 施設利用料（拠点）
    # ── 介護付有料老人ホーム（特定施設）──
    '1660': '介護付有料',            # 特定施設入居者生活介護
    '1680': '介護付有料',            # 特定施設入居者生活介護（自費）
    '2130': '介護付有料',            # 特定施設入居者生活介護（家賃）
    # ── 認知症GH（認知症対応型共同生活介護）──
    '1710': '認知症GH',
    '1711': '認知症GH',
    '2160': '認知症GH',              # 認知症対応型共同生活介護（家賃）
    # ── 特養・ショートステイ（蒼天会）──
    '1690_1691_2150': '特養',        # 地域密着型介護老人福祉施設（小規模特養）
    '1692_2150_短期': '特養',        # 短期入所生活介護（ショートステイ / SS）
    '2280': '特養',                  # 施設利用料（特養）
    # ── その他事業 ──
    '2201': 'その他',                # ITコンサルティング
    '2220': 'その他',                # デモリサーチ
    '2230': 'その他',                # 施設賃料（ビル）
    '0000': 'その他',                # 立替金
}


def smart_facility_badge(name, codes_str):
    """
    Determine the badge label for a facility using BOTH name AND service codes.
    This is more accurate than name-only since 拠点 are multi-service hubs.
    """
    n = name or ''
    codes = set((codes_str or '').split(','))

    # Single-purpose facilities → name is reliable
    if 'GH' in n and '認知症' not in n:     return '障がい者GH'
    if '訪問介護' in n:                      return '訪問介護'
    if '訪問看護' in n:                      return '訪問看護'
    if '就労' in n:                          return '就労支援'
    if '居宅介護支援' in n:                  return '相談・CM'
    if '相談支援' in n:                      return '相談・CM'
    if 'ケアテクノロジー' in n:              return '福祉用具'
    if 'リサーチ' in n:                      return 'その他'
    if 'IT' in n or 'コンサル' in n:         return 'その他'
    if n in ('けやきビル', 'さくらビル'):       return 'その他'
    if n == '0043_つばき苑':                   return '特養'

    # Multi-service 拠点 → use service codes to describe composite nature
    has_day  = bool(codes & {'1110','1120','1130','1140'})
    has_yuro = bool(codes & {'2110','2290'})      # 住宅型有料
    has_sa   = '2120' in codes                    # サ高住
    has_toku = bool(codes & {'1660','2130'})       # 介護付有料（特定施設）
    has_gh   = bool(codes & {'1710','2160'})       # 認知症GH
    has_ss   = bool(codes & {'1690_1691_2150','1692_2150_短期'})

    if has_toku and has_gh:  return '介護付有料+認知症GH'
    if has_toku:              return '介護付有料'
    if has_gh:                return '認知症GH'
    if has_ss:                return '特養'
    if has_yuro and has_day: return '有料+デイ'
    if has_sa  and has_day:  return 'サ高住+デイ'
    if has_yuro:              return '有料老人ホーム'
    if has_sa:                return 'サ高住'
    return 'その他'


# Exact sales_type → category mapping (no text search)
SALES_TYPE_CATEGORY = {
    'その他':                         'その他',
    'サ高住':                         '有料老人ホーム',
    'デイサービス（自費）':                '通所介護',
    '共同生活援助':                     '障がい者GH',
    '共同生活援助(受託居宅介護以外)':        '障がい者GH',
    '地域密着型介護老人福祉施設':            '特養',
    '地域密着型通所介護':                 '通所介護',
    '地方自治体独自事業（移動支援）':          '訪問介護',
    '就労事業収入（洗濯）':               '就労支援',
    '就労事業収入（食事提供）':             '就労支援',
    '就労継続支援B型':                  '就労支援',
    '就労選択支援':                     '就労選択',
    '居宅介護支援':                     '相談・CM',
    '有料老人ホーム':                    '有料老人ホーム',
    '特定施設入居者生活介護':              '介護付有料',
    '特定施設入居者生活介護（施設）':          '介護付有料',
    '生活介護（デイナイトケア）':             '通所介護',
    '相談支援':                        '相談・CM',
    '短期入所':                        '特養',
    '福祉用具販売':                     '福祉用具',
    '福祉用具貸与':                     '福祉用具',
    '福祉用具貸与（自費）':               '福祉用具',
    '訪問介護':                        '訪問介護',
    '訪問介護（全額自費）':               '訪問介護',
    '訪問介護（障害福祉サービス）':           '訪問介護',
    '訪問看護（介護）':                  '訪問看護',
    '訪問看護（医療_国保連）':             '訪問看護',
    '訪問看護（医療_支払基金）':            '訪問看護',
    '訪問看護（自費）':                  '訪問看護',
    '認知症対応型共同生活介護':             '認知症GH',
    '認知症対応型共同生活介護（施設）':         '認知症GH',
    # Facility-level revenue types (from sales_summary_data)
    'ITコンサルティング事業':              'その他',
    'リサーチ事業収入':                   'その他',
    '施設賃料（ビル）':                   'その他',
    '施設利用料（特養）':                  '特養',
    '施設利用料（その他）':                 '有料老人ホーム',
    '就労事業収入（食事・福岡）':             '就労支援',
    '就労継続支援（自己負担）':              '就労支援',
    '特定施設入居者生活介護（家賃）':           '介護付有料',
    'GH家賃':                         '障がい者GH',
    'サービス付き高齢者向け住宅':             '有料老人ホーム',
    '地域密着型特別養護老人ホーム':            '特養',
    '通所介護':                        '通所介護',
}

def debit_side(debit_account):
    """Classify a sales_fmt_data.debit_account value into 'insurance' or 'self_pay'.

    `6115_保険未収金` → insurance. Everything else (6120_利用者自己負担未収金,
    7107_仮受金, 6160_未収入金, 6110_売掛金, null/empty) → self_pay. The 6115
    prefix is the only one that consistently represents an insurer-paid
    receivable; the remaining types are either user-paid or facility-level
    adjustments, which we roll up under self_pay for display."""
    if debit_account and debit_account.startswith('6115'):
        return 'insurance'
    return 'self_pay'


def categorize_service(sales_type):
    """Map sales_type → category. Uses exact lookup first, text fallback for unknown types."""
    if not sales_type:
        return 'その他'
    cat = SALES_TYPE_CATEGORY.get(sales_type)
    if cat:
        return cat
    # Fallback for unknown sales_types
    s = sales_type
    if '訪問介護' in s: return '訪問介護'
    if '訪問看護' in s: return '訪問看護'
    if '認知症' in s: return '認知症GH'
    if '共同生活援助' in s: return '障がい者GH'
    if '特定施設' in s: return '介護付有料'
    if '通所' in s or 'デイ' in s: return '通所介護'
    if '短期入所' in s: return '特養'
    if '有料老人ホーム' in s or 'サ高住' in s: return '有料老人ホーム'
    if '就労選択' in s: return '就労選択'
    if '就労' in s: return '就労支援'
    if '居宅介護支援' in s or '相談支援' in s: return '相談・CM'
    if '福祉用具' in s: return '福祉用具'
    if '移動支援' in s: return '訪問介護'
    return 'その他'


# ── Analysis / Upsell ─────────────────────────────────────────────────────────

# Services that are good upsell targets for residential customers
UPSELL_SERVICES = {
    'デイ':     ('通所介護', ['1110', '1120', '1130', '1140']),
    '訪問介護': ('訪問介護',         ['1210', '1230', '1290']),
    '訪問看護': ('訪問看護',         ['1310_1320_1330', '1340']),
    '福祉用具': ('福祉用具',         ['1640', '1641', '1650']),
}

# Residential provider types that are upsell candidates
UPSELL_RESIDENTIAL = ('住宅型有料老人ホーム', 'サービス付き高齢者向け住宅')

# Same set expressed as sales_type strings (used post-migration where
# occ rows carry sales_type in provider_names instead of csm.provider_name).
UPSELL_RESIDENTIAL_SALES_TYPES = ('有料老人ホーム', 'サ高住')

# Care insurance monthly unit limits by care level (FY2024 厚生労働省基準)
# These are in UNITS, not yen — yen value depends on regional unit price.
CARE_LEVEL_LIMITS = {
    '要支援1': 5_032, '要支援2': 10_531,
    '要介護1': 16_765, '要介護2': 19_705,
    '要介護3': 27_048, '要介護4': 30_938, '要介護5': 36_217,
}

# Regional unit prices by service category (地域区分 × 人件費割合, FY2024).
# 人件費割合: 70% = 訪問系, 55% = リハ/短期入所/小規模多機能, 45% = 通所/施設/福祉用具
# Source: 厚生労働省 介護給付費単位数等サービスコード表
REGION_UNIT_PRICES_BY_CATEGORY = {
    # region: {70: rate, 55: rate, 45: rate}
    '鹿児島': {70: 10.42, 55: 10.33, 45: 10.27},  # 6級地
    '福岡':   {70: 10.84, 55: 10.66, 45: 10.54},  # 4級地
    '宇美':   {70: 10.21, 55: 10.17, 45: 10.14},  # 7級地
}
DEFAULT_REGION_RATES = {70: 10.42, 55: 10.33, 45: 10.27}  # 6級地 default

# Map sales_type to 人件費割合 category
SALES_TYPE_COST_RATIO = {
    '訪問介護':           70,
    '訪問看護（介護）':    70,
    '通所介護':           45,
    '地域密着型通所介護':  45,
    '福祉用具貸与':       45,  # 福祉用具 is 全国一律10.00 but we approximate
    '短期入所':           55,
}

# Keep backward-compatible flat rate for simple lookups
REGION_UNIT_PRICES = {
    '鹿児島': 10.27,
    '福岡':   10.70,
    '宇美':   10.14,
}
DEFAULT_UNIT_PRICE = 10.27

# Map office_name → region key.
# Facilities not listed default to 鹿児島.
OFFICE_REGION = {
    'ひがし':   '福岡',
    'こまくさ': '福岡',
    'みどり1':  '福岡',
    'しらかば': '福岡',
    'ゆうひ':   '宇美',
    'つばき':   '福岡',
}

def _facility_category(provider_names_str):
    """Derive display category from merged provider names."""
    names = set((provider_names_str or '').split(','))
    cats = sorted({PROVIDER_CATEGORY.get(n, n) for n in names})
    return '+'.join(cats) if cats else 'その他'


def _matches_provider(sales_type, provider_name):
    """Check if a sales_type matches a provider's service."""
    keywords = PROVIDER_SALES_MATCH.get(provider_name, [])
    return any(kw in sales_type for kw in keywords) if keywords else False


def get_unit_price(office_name=None):
    """Return the regional unit price for an office (flat average). Defaults to 鹿児島."""
    region = OFFICE_REGION.get(office_name)
    if region:
        return REGION_UNIT_PRICES.get(region, DEFAULT_UNIT_PRICE)
    return DEFAULT_UNIT_PRICE


def estimate_units_from_bills(bills_by_sales_type, region='鹿児島'):
    """Convert per-sales_type yen amounts to estimated units using correct per-service unit prices.
    bills_by_sales_type: dict {sales_type: yen_amount}
    Returns (total_units, weighted_avg_unit_price)."""
    rates = REGION_UNIT_PRICES_BY_CATEGORY.get(region, DEFAULT_REGION_RATES)
    total_units = 0
    total_yen = 0
    for stype, yen in bills_by_sales_type.items():
        if yen <= 0:
            continue
        ratio = SALES_TYPE_COST_RATIO.get(stype, 45)
        price = rates.get(ratio, rates[45])
        total_units += yen / price
        total_yen += yen
    avg_price = total_yen / total_units if total_units > 0 else rates[45]
    return total_units, avg_price

# sales_type strings that count against the care insurance monthly limit (支給限度額).
# Excludes: 障害福祉サービス, 医療保険, 自費, 施設サービス (特定施設・特養).
CARE_LIMIT_SALES_TYPES = {
    '訪問介護',
    '通所介護',
    '地域密着型通所介護',
    '訪問看護（介護）',
    '福祉用具貸与',
    '短期入所',
}

def infer_care_level(care_ins_yen, unit_price=None):
    """
    Infer the minimum care level from total yen spending (flat unit price).
    Kept for backward compatibility. Prefer infer_care_level_units() for accuracy.
    """
    if care_ins_yen <= 0:
        return '自費のみ', 0
    up = unit_price or DEFAULT_UNIT_PRICE
    for level in ['要支援1','要支援2','要介護1','要介護2','要介護3','要介護4','要介護5']:
        limit_yen = CARE_LEVEL_LIMITS[level] * up
        if care_ins_yen <= limit_yen:
            return level, limit_yen
    # 要介護5 is the maximum level — excess is paid out-of-pocket (限度額超過).
    return '要介護5', CARE_LEVEL_LIMITS['要介護5'] * up


def infer_care_level_units(total_units):
    """
    Infer care level from estimated consumed units. More accurate than yen-based
    because it accounts for per-service unit price differences.
    Returns (level_name, limit_units).
    """
    if total_units <= 0:
        return '自費のみ', 0
    for level in ['要支援1','要支援2','要介護1','要介護2','要介護3','要介護4','要介護5']:
        if total_units <= CARE_LEVEL_LIMITS[level]:
            return level, CARE_LEVEL_LIMITS[level]
    return '要介護5', CARE_LEVEL_LIMITS['要介護5']


def infer_care_level_from_units(total_units):
    """Infer care level from actual consumed units (from person_billing_monthly).
    More accurate than infer_care_level() which guesses from yen spending."""
    if total_units <= 0:
        return '自費のみ', 0
    for level in ['要支援1','要支援2','要介護1','要介護2','要介護3','要介護4','要介護5']:
        if total_units <= CARE_LEVEL_LIMITS[level]:
            return level, CARE_LEVEL_LIMITS[level]
    return '要介護5', CARE_LEVEL_LIMITS['要介護5']


# ── Provider type sets and office classification ──────────────────────────────

RESIDENTIAL_PROVIDERS = set(RESIDENTIAL) | set(RESIDENTIAL_SALES_PATTERNS) | {
    # Extra sales_type variants seen in sales_summary (exact strings, not
    # patterns — these join RESIDENTIAL_PROVIDERS for _office_type matching).
    '有料老人ホーム', 'サ高住',
    '特定施設入居者生活介護', '特定施設入居者生活介護（施設）',
    '共同生活援助', '共同生活援助(受託居宅介護以外)',
    '認知症対応型共同生活介護', '認知症対応型共同生活介護（施設）',
    '地域密着型介護老人福祉施設', '特養', '短期入所',
}
VISIT_PROVIDERS = {'訪問介護', '訪問看護', '訪問看護（介護）',
                   '訪問看護（医療_国保連）', '訪問看護（医療_支払基金）'}
EQUIPMENT_PROVIDERS = {'福祉用具', '福祉用具貸与', '福祉用具貸与（自費）'}
DAY_PROVIDERS = {'通所介護', '地域密着型通所介護', 'デイサービス（自費）',
                 '就労継続支援B型', '就労選択支援', '生活介護（デイナイトケア）'}
SUPPORT_PROVIDERS = {'居宅介護支援', '相談支援', '短期入所生活介護', '介護施設紹介'}
CORP_PROVIDERS = {'ITコンサルティング事業', '洗濯代行'}

def _office_type(provider_names_str):
    """Classify an office as 居住系/訪問系/通所・就労/福祉用具/相談等/事業 based on its provider types."""
    names = set((provider_names_str or '').split(','))
    has_res   = bool(names & RESIDENTIAL_PROVIDERS)
    has_visit = bool(names & VISIT_PROVIDERS)
    has_day   = bool(names & DAY_PROVIDERS)
    has_equip = bool(names & EQUIPMENT_PROVIDERS)
    has_corp  = bool(names & CORP_PROVIDERS)
    has_support = bool(names & SUPPORT_PROVIDERS)
    if has_res:
        return '居住系'
    if has_visit:
        return '訪問系'
    if has_day:
        return '通所・就労'
    if has_support:
        return '相談・CM'
    if has_equip and not has_corp:
        return '福祉用具'
    if has_corp:
        return '事業・本社'
    return 'その他'
