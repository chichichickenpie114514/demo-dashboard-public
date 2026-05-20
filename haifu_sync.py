"""
Pull the 「配賦マトリクス」 allocation matrix from Google Sheets via the
Cloud Run Job's default service account.

Source: Google Sheets (configured via HAIFU_SHEET_ID env var).

The Sheet must be shared (Viewer) with the snapshot-monthly Job's SA:
    <YOUR_SERVICE_ACCOUNT_EMAIL>

Sheet structure (gid=238489130, tab '配賦マトリクス'):
    Row 1: facility short labels (cols 6-18 = destinations, cols 19-26 = sources)
    Row 2: 拠点CODE
    Row 3: '売上CODE' / '売上種別' / 事業所labels
    Row 4..: data, one row per (year × month × 売上CODE)
        Col A: '2026年' / Col B: '3月' / Col C: 'a120' / Col E: '訪問介護（鹿）'
        Cols F-R: positive amounts to destinations
        Cols S-Z: negative total per source (= -Σ destinations)
"""

import os
import json

SHEET_ID = os.environ.get('HAIFU_SHEET_ID', '')
SHEET_GID = 238489130
SHEET_RANGE = "'配賦マトリクス'!A1:AR2000"

CACHE_PATH = os.path.join(os.path.dirname(__file__), 'haifu_cache.json')

# Column indices (1-based, matching openpyxl semantics) — kept here so callers
# can verify against the Sheet without re-reading the comment.
DEST_COL_RANGE = (6, 18)    # inclusive
SOURCE_COL_RANGE = (19, 26)  # inclusive
COL_YEAR = 1
COL_MONTH = 2
COL_CODE = 3
COL_SVC_TYPE = 5


def fetch():
    """Demo mode: return empty list (no Google Sheets data available)."""
    return []


def _normalize_month(year_cell, month_cell):
    """('2026年', '3月') → '2026-03'. Return None on parse failure."""
    if not year_cell or not month_cell:
        return None
    try:
        y = str(year_cell).replace('年', '').strip()
        m = str(month_cell).replace('月', '').strip()
        return f'{int(y):04d}-{int(m):02d}'
    except (ValueError, TypeError):
        return None


def _to_float(v):
    """Coerce a Sheets cell value to float; '' / None / non-numeric → 0.0."""
    if v is None or v == '':
        return 0.0
    try:
        # Sheets API returns numbers as strings unless valueRenderOption is set.
        s = str(v).replace(',', '').strip()
        if not s:
            return 0.0
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def parse(rows):
    """Convert raw Sheet rows to a list of allocation records.

    Returns: [{
        'month': 'YYYY-MM',
        'code': 'a120',
        'service_type': '訪問介護（鹿）',
        'destinations': {<dest_label>: amount, ...},   # positive
        'sources':      {<source_label>: amount, ...}, # negative
    }, ...]

    The dest/source labels are the row-1 Sheet header strings; mapping to
    canonical office names happens later (in snapshot.py, via
    config.HAIFU_DESTINATION_TO_OFFICE / HAIFU_SOURCE_TO_OFFICE).
    """
    if not rows:
        return []
    if len(rows) < 4:
        # No data rows.
        return []

    header_row = rows[0]  # 0-based: this is Sheet row 1
    # Sheets API returns lists with possibly fewer cells than max columns.
    # Pad to a safe length so 1-based column indices into a 0-based list work.
    def cell(row, col_1based):
        idx = col_1based - 1
        if idx < 0 or idx >= len(row):
            return None
        return row[idx]

    # Resolve destination / source label maps from the header row.
    dest_labels = {}
    source_labels = {}
    for col in range(DEST_COL_RANGE[0], DEST_COL_RANGE[1] + 1):
        v = cell(header_row, col)
        if v:
            dest_labels[col] = str(v).strip()
    for col in range(SOURCE_COL_RANGE[0], SOURCE_COL_RANGE[1] + 1):
        v = cell(header_row, col)
        if v:
            source_labels[col] = str(v).strip()

    out = []
    # Data rows start from Sheet row 4 (index 3).
    for row in rows[3:]:
        if not row:
            continue
        month = _normalize_month(cell(row, COL_YEAR), cell(row, COL_MONTH))
        if not month:
            continue
        code = cell(row, COL_CODE)
        svc_type = cell(row, COL_SVC_TYPE)
        if not code:
            continue

        destinations = {}
        for col, label in dest_labels.items():
            amt = _to_float(cell(row, col))
            if amt:
                destinations[label] = amt
        sources = {}
        for col, label in source_labels.items():
            amt = _to_float(cell(row, col))
            if amt:
                sources[label] = amt

        # Skip rows with no allocation activity at all.
        if not destinations and not sources:
            continue

        out.append({
            'month': month,
            'code': str(code).strip(),
            'service_type': str(svc_type).strip() if svc_type else '',
            'destinations': destinations,
            'sources': sources,
        })
    return out


def save_cache(records):
    with open(CACHE_PATH, 'w', encoding='utf-8') as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def load_cache():
    if not os.path.exists(CACHE_PATH):
        return []
    with open(CACHE_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


if __name__ == '__main__':
    # Smoke test: fetch + parse + dump first few records.
    rows = fetch()
    records = parse(rows)
    print(f'Fetched {len(rows)} rows, parsed {len(records)} allocation records.')
    for r in records[:3]:
        print(json.dumps(r, ensure_ascii=False, indent=2))
