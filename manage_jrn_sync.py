"""Pull the management-accounting manual-entry journal from a local xlsx.

Sheet: 管理会計仕訳DB(手入力) inside `002-2_管理会計仕訳_手入力シート.xlsx`.
Filter to the 5 sales-code (科目CD) that the dashboard surfaces:
  1650 福祉用具販売
  2220 リサーチ事業収入
  2240 就労事業収入（食事提供）
  2245 就労事業収入（その他）
  2299 就労事業収入（食事・福岡）

These items are NOT in billing_records / journal_entries, so the standard 拠点
views drastically under-count facilities like 就労継続支援事業所（あかね）
(~¥249M annual). The 拠点（配賦2）tab adds these on top of the existing
haifu view to give a fuller management-accounting picture.

MVP loads the xlsx directly (the SA does not yet have Viewer access to the
live Google Sheet — `<YOUR_MANAGE_JRN_SHEET_ID>`).
A future swap to the Sheets API only needs to replace `fetch()`.
"""
import os
import openpyxl

XLSX_PATH = os.path.join(os.path.dirname(__file__), '002-2_管理会計仕訳_手入力シート.xlsx')
SHEET_NAME = '管理会計仕訳DB(手入力)'
TARGET_CODES = {'1650', '2220', '2240', '2245', '2299'}


def fetch():
    """Demo mode: return empty list (xlsx file not available)."""
    return []


def parse(rows):
    """Return [{month, code, name, facility, amount}, ...].

    - Header rows live at indices 0-2; data starts at index 3.
    - Skips negative amounts (user states "no negatives expected"; 3 rows
      actually have negatives in the wild — log them so we can investigate).
    - month format: 'YYYY-MM' to match the rest of the snapshot pipeline.
    """
    out = []
    skipped_neg = 0
    skipped_other = 0
    for r in rows[3:]:
        if len(r) < 9:
            skipped_other += 1
            continue
        year_s = str(r[1] or '').replace('年', '')
        mon_s = str(r[2] or '').replace('月', '')
        try:
            month = f'{int(year_s):04d}-{int(mon_s):02d}'
            code = str(r[4]).strip()
            amt_raw = r[8]
            if isinstance(amt_raw, (int, float)):
                amt = float(amt_raw)
            else:
                amt = float(str(amt_raw).replace(',', '').replace('¥', '').strip() or 0)
        except (ValueError, IndexError, TypeError):
            skipped_other += 1
            continue
        if code not in TARGET_CODES:
            continue
        if amt < 0:
            skipped_neg += 1
            continue
        out.append({
            'month': month,
            'code': code,
            'name': str(r[5] or '').strip(),       # 科目名 (F)
            'facility': str(r[7] or '').strip(),   # 部門名 (H)
            'amount': round(amt),
        })
    if skipped_neg:
        print(f'manage_jrn_sync: skipped {skipped_neg} negative-amount rows')
    print(f'manage_jrn_sync: parsed {len(out)} matching rows '
          f'(target codes: {sorted(TARGET_CODES)})')
    return out
