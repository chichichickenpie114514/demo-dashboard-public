"""DeepSeek-backed AI analysis helper (OpenAI-compatible API).

Called by /api/ai-analysis in server.py. Uses the OpenAI SDK pointed at
DeepSeek's API endpoint for scope-specific data analysis.

Configuration via env vars:
  DEEPSEEK_API_KEY (required) — DeepSeek API key
  DEEPSEEK_MODEL   (optional) — default `deepseek-v4-flash`
  OPENAI_API_KEY / OPENAI_MODEL — fallback env var names

The system prompt itself lives in `docs/AI_SYSTEM_PROMPT.md` so non-developers
can edit AI behavior by editing that single markdown file.
"""
import os
import json
from openai import OpenAI

DEEPSEEK_MODEL = os.getenv('DEEPSEEK_MODEL', os.getenv('OPENAI_MODEL', 'deepseek-v4-flash'))
_client = OpenAI(
    api_key=os.getenv('DEEPSEEK_API_KEY', os.getenv('OPENAI_API_KEY', '')),
    base_url='https://api.deepseek.com',
)

# ── Load the system prompt from docs/AI_SYSTEM_PROMPT.md ─────────────────────
_PROMPT_PATH = os.path.join(os.path.dirname(__file__), 'docs', 'AI_SYSTEM_PROMPT.md')
_FALLBACK_PROMPT = (
    'あなたは介護事業の経営ダッシュボード専属データアナリストです。'
    'データから経営判断に役立つ観察を 3-5 点、簡潔な箇条書き（先頭 - で開始）で日本語出力。'
    '配賦の方向は「流入」「流出」のみ使用。数値根拠を必ず含める。'
)
try:
    with open(_PROMPT_PATH, encoding='utf-8') as _f:
        SYSTEM_PROMPT = _f.read()
    print(f'[AI] loaded system prompt from {_PROMPT_PATH} ({len(SYSTEM_PROMPT)} chars)')
except FileNotFoundError:
    SYSTEM_PROMPT = _FALLBACK_PROMPT
    print(f'[AI] WARN: prompt file not found at {_PROMPT_PATH}, using minimal fallback')

# Scope-specific focus hint, prepended to the user message. Keeps the system
# prompt file general while letting each scope nudge attention without
# hot-reloading the whole prompt.
SCOPE_INSTRUCTIONS = {
    'global':            '全社売上トレンド、保険・自費比率、前月比異常、新規/終了の動き',
    'tab:facilities':    '拠点別売上の伸長/減退、業態構成の偏り、上位/下位拠点の特徴',
    'tab:facilities-compare':'拠点比較グラフ（ユーザーが選んだ拠点群の月別推移）。上昇/下降の拠点、シェアの変化、相対値モードでは成長率の差',
    'tab:analysis':      '保険利用率、保険未使用枠、低利用率の居住者層、クロスセル機会',
    'tab:services':      'サービス別の利用者集中度、売上シェア、平均単価の高低',
    'tab:persons':       '上位利用者層、サービス利用数の分布、自費比率の高い利用者',
    'tab:history':       '季節性、トレンド転換点、伸長/減退カテゴリ・拠点',
    'tab:card-view':     '配賦込み拠点ランキング、配賦の規模が示す人員稼働、居住拠点とステーションの役割分担',
    'tab:card-view2':    '管理会計収入を含めた拠点ランキング。就労支援・福祉用具・リサーチ等、通常 billing データに乗らない収入が積み上がる拠点に注目',
    'modal:facility':    '当該拠点の業態構成・利用者動向・推移',
    'modal:card-detail': 'サービス別請求の特徴、配賦が示すスタッフ稼働傾向、利用者・自治体請求のミックス',
    'modal:card-detail2':'当該拠点の請求 + 配賦 + 管理会計収入。3 系統の構成比、特に管理会計収入が大きい拠点の業態的位置づけ',
    'modal:person':      'この利用者の請求パターン、主担当事業所、月次推移',
    'modal:care-level':  '該当推定介護度の利用者層の特徴、利用率分布、未使用枠の偏り',
    'modal:service-users':'当該 (拠点 × サービス) の利用者特徴、上位、自費比率',
    'modal:fac-gap':     '当該拠点の保険利用率 / 未使用枠 / 低利用率者数、業態的位置づけ',
    'tab:map':           '施設の地理的分布、地域別売上構成、都市圏ごとの特徴、訪問系拠点のカバレッジ',
}

# Human-readable view names. Tells the model "what page the user is looking
# at right now" so it can frame the analysis to that context.
SCOPE_DISPLAY = {
    'global':            'ヘッダー global ビュー（全社 KPI + 前月比アラート、期間内全月）',
    'tab:facilities':    '「拠点」タブ（全社の拠点別売上一覧、配賦/管理会計収入は含まない標準集計）',
    'tab:facilities-compare':'「拠点比較グラフ」（拠点タブ下部、ユーザーが選んだ拠点だけを月別推移で重ねて比較。カテゴリ絞り込み・一人当たり・絶対値/相対値の各モード対応）',
    'tab:analysis':      '「分析」タブ（保険利用率・未使用枠・営業機会、推定介護度ベース）',
    'tab:services':      '「サービス」タブ（サービス種別ごとの売上と利用者数）',
    'tab:persons':       '「利用者」タブ（1881 名 → 上位 50 名 + 統計サマリで送信）',
    'tab:history':       '「推移」タブ（月次の全社・カテゴリ別・拠点別推移）',
    'tab:card-view':     '「拠点（配賦）」タブ（請求データ + 社内間配賦込みの拠点ビュー、card / chart 両モード）',
    'tab:card-view2':    '「拠点（配賦2）」タブ（拠点（配賦）+ 管理会計仕訳DB(手入力) の収入を上乗せ、card / chart 両モード）',
    'modal:facility':    '「拠点詳細モーダル」（拠点タブから 1 拠点を drill-down、residents/users 上位 30）',
    'modal:card-detail': '「カード詳細モーダル」（拠点（配賦）タブから 1 拠点を drill-down、配賦行 mute 連動）',
    'modal:card-detail2':'「カード詳細モーダル」（拠点（配賦2）タブから 1 拠点を drill-down、管理会計収入セクションも表示）',
    'modal:person':      '「利用者詳細モーダル」（1 利用者の月次請求明細・契約サービス）',
    'modal:care-level':  '「介護度別利用者モーダル」（分析タブのドーナツから drill-down、推定介護度ごとの居住者リスト上位 50）',
    'modal:service-users':'「サービス利用者モーダル」（拠点詳細から特定サービスの利用者を drill-down、上位 30）',
    'modal:fac-gap':     '「拠点ギャップモーダル」（分析タブから 1 拠点の保険利用分析を drill-down）',
    'tab:map':           '「マップ」タブ（施設の地理的分布、地域別の拠点配置・売上規模）',
}


def _summarize_filters(filters):
    """Render the UI-side filter state as a short Japanese phrase."""
    if not filters:
        return 'なし'
    parts = []
    cat = filters.get('category')
    if cat and cat != 'all':
        parts.append(f'カテゴリ chip = 「{cat}」')
    n_cat = len(filters.get('cat_mutes') or [])
    if n_cat: parts.append(f'カテゴリ mute = {n_cat}件')
    n_row = len(filters.get('row_mutes') or [])
    if n_row: parts.append(f'行 mute = {n_row}件')
    n_cust = len(filters.get('cust_mutes') or [])
    if n_cust: parts.append(f'請求元 mute = {n_cust}件')
    n_haifu = len(filters.get('haifu_mutes') or [])
    if n_haifu: parts.append(f'配賦行 mute = {n_haifu}件')
    # facilities-compare specific
    sel = filters.get('selected')
    if sel: parts.append(f'選択拠点 = {len(sel)}件')
    if filters.get('per_user'):
        parts.append('一人当たりモード')
    if filters.get('y_axis') == 'rel':
        parts.append('相対値モード(開始月=100)')
    # services
    if filters.get('sort'):
        parts.append(f"並び替え = {filters['sort']}")
    # persons search
    if filters.get('q'):
        parts.append(f"検索 = 「{filters['q']}」")
    # history mode + cat select
    if filters.get('mode') in ('category', 'facility'):
        parts.append(f"表示モード = {filters['mode']}")
    cats = filters.get('categories') or []
    if cats and 'selected' not in filters:  # avoid double-count with compare
        parts.append(f'カテゴリ選択 = {len(cats)}件')
    return ' / '.join(parts) if parts else 'なし'


def _build_prompt(scope, data, period_label, entity_id='', filters=None):
    view_label = SCOPE_DISPLAY.get(scope, scope)
    entity_line = f"[対象エンティティ] {entity_id}" if entity_id else "[対象エンティティ] (なし — タブ全体)"
    filter_line = f"[適用フィルタ] {_summarize_filters(filters)}"
    user_prompt = (
        f"[ビュー] {view_label}\n"
        f"[scope_id] {scope}\n"
        f"{entity_line}\n"
        f"[期間] {period_label}\n"
        f"{filter_line}\n"
        f"[着眼点] {SCOPE_INSTRUCTIONS.get(scope, '')}\n"
        f"[データ]\n{json.dumps(data, ensure_ascii=False, separators=(',', ':'))}"
    )
    return [
        {'role': 'system', 'content': SYSTEM_PROMPT},
        {'role': 'user', 'content': user_prompt},
    ], user_prompt


def analyze_stream(scope: str, data: dict, period_label: str, entity_id: str = '', filters: dict = None):
    """Yield visible content chunks as they arrive from OpenAI.

    Caller is expected to accumulate the chunks and cache the full text.
    Final-chunk usage stats are logged via Cloud Run.
    """
    messages, user_prompt = _build_prompt(scope, data, period_label, entity_id, filters)
    print(f'[AI] streaming start prompt_chars system={len(SYSTEM_PROMPT)} user={len(user_prompt)}')
    stream = _client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=messages,
        max_completion_tokens=4000,
        stream=True,
    )
    visible_chars = 0
    finish_reason = None
    last_usage = None
    for chunk in stream:
        if getattr(chunk, 'usage', None):
            last_usage = chunk.usage
        if not chunk.choices:
            continue
        ch = chunk.choices[0]
        delta = getattr(ch, 'delta', None)
        if delta and getattr(delta, 'content', None):
            yield delta.content
            visible_chars += len(delta.content)
        if ch.finish_reason:
            finish_reason = ch.finish_reason

    if last_usage:
        det = getattr(last_usage, 'completion_tokens_details', None)
        rt = getattr(det, 'reasoning_tokens', None) if det else None
        print(f'[AI] streaming done finish={finish_reason} visible_chars={visible_chars} '
              f'prompt_tokens={last_usage.prompt_tokens} completion_tokens={last_usage.completion_tokens} '
              f'reasoning_tokens={rt}')
    else:
        print(f'[AI] streaming done finish={finish_reason} visible_chars={visible_chars} '
              f'(no usage in stream)')


def analyze(scope: str, data: dict, period_label: str, entity_id: str = '', filters: dict = None) -> str:
    """Non-streaming fallback. Accumulates a stream into a single string."""
    return ''.join(analyze_stream(scope, data, period_label, entity_id, filters)).strip()
