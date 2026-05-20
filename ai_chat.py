"""AI Chat backend — general-purpose chat about the dashboard + data.
Uses DeepSeek V4 Pro with function calling for on-demand data access.

Called by /api/ai-chat in server.py.
"""
import os
import json
import copy
from openai import OpenAI

CHAT_MODEL = os.getenv('DEEPSEEK_CHAT_MODEL', 'deepseek-v4-pro')
_client = OpenAI(
    api_key=os.getenv('DEEPSEEK_API_KEY', os.getenv('OPENAI_API_KEY', '')),
    base_url='https://api.deepseek.com',
)

# ── Load system prompt ───────────────────────────────────────────────────────
_PROMPT_PATH = os.path.join(os.path.dirname(__file__), 'docs', 'AI_CHAT_PROMPT.md')
try:
    with open(_PROMPT_PATH, encoding='utf-8') as _f:
        SYSTEM_PROMPT = _f.read()
    print(f'[CHAT] loaded prompt from {_PROMPT_PATH} ({len(SYSTEM_PROMPT)} chars)')
except FileNotFoundError:
    SYSTEM_PROMPT = 'あなたは BI ダッシュボード専属の AI アシスタントです。データ分析とダッシュボード構造についてのみ回答してください。'
    print(f'[CHAT] WARN: prompt file not found, using fallback')

# ── Store reference (set by server.py) ────────────────────────────────────────
_store = None
_available_months = []


def init(store):
    """Called by server.py to inject the data store."""
    global _store, _available_months
    _store = store
    meta = store.get('months') or []
    _available_months = sorted(
        m if isinstance(m, str) else m.get('month') for m in meta
    )


# ── Tool implementations ──────────────────────────────────────────────────────

def _check_range(start, end):
    """Validate date range. Returns (ok, error_message)."""
    if not _available_months:
        return False, 'データが読み込まれていません。'
    if start < _available_months[0] or end > _available_months[-1]:
        return False, (
            f'指定された期間がデータ範囲外です。'
            f'利用可能な期間: {_available_months[0]} 〜 {_available_months[-1]}'
        )
    if start > end:
        start, end = end, start
    return True, ''


def _months_in_range(start, end):
    return sorted(m for m in _available_months if start <= m <= end)


def _sum_num(*vals):
    return sum(v for v in vals if isinstance(v, (int, float)))


def tool_get_kpi(start, end):
    """Monthly KPI data for the given period."""
    ok, err = _check_range(start, end)
    if not ok:
        return {'error': err}
    months = _months_in_range(start, end)
    per = [_store.get(f'{m}/kpi') or {} for m in months]
    if not per:
        return {'error': '該当期間のデータがありません。'}
    out = dict(per[-1] if per else {})
    for k in ('total', 'insurance', 'self_pay', 'new_customers', 'lost_customers'):
        out[k] = _sum_num(*(p.get(k) or 0 for p in per))
    out['customers'] = per[-1].get('customers', 0)
    out['facilities'] = per[-1].get('facilities', 0)
    out['period'] = f'{months[0]}〜{months[-1]}'
    return out


def tool_get_facilities(start, end):
    """Per-facility billing data."""
    ok, err = _check_range(start, end)
    if not ok:
        return {'error': err}
    months = _months_in_range(start, end)
    per_month = [_store.get(f'{m}/facilities_billing') or [] for m in months]
    merged = {}
    for rows in per_month:
        for r in rows:
            name = r.get('name')
            if not name:
                continue
            if name not in merged:
                merged[name] = copy.deepcopy(r)
            else:
                acc = merged[name]
                for k in ('total', 'insurance', 'self_pay'):
                    acc[k] = (acc.get(k) or 0) + (r.get(k) or 0)
    result = sorted(merged.values(), key=lambda f: -(f.get('total') or 0))
    # Trim to top 15 + return lightweight
    return [
        {'name': f['name'], 'category': f.get('category', ''),
         'total': f['total'], 'insurance': f['insurance'], 'self_pay': f['self_pay'],
         'active': f.get('active', 0), 'categories': f.get('categories', [])}
        for f in result[:15]
    ]


def tool_get_analysis(start, end):
    """Analysis data (care levels, insurance utilization, pipeline, tenure)."""
    ok, err = _check_range(start, end)
    if not ok:
        return {'error': err}
    months = _months_in_range(start, end)
    end_data = _store.get(f'{months[-1]}/analysis') or {}
    return {
        'care_level_distribution': end_data.get('care_level_distribution', {}),
        'total_ins_persons': end_data.get('total_ins_persons', 0),
        'total_self_pay': end_data.get('total_self_pay', 0),
        'total_low_util': end_data.get('total_low_util', 0),
        'total_potential': end_data.get('total_potential', 0),
        'avg_tenure': end_data.get('avg_tenure', 0),
        'total_pipeline': end_data.get('total_pipeline', 0),
        'categories': end_data.get('categories', []),
        'period': f'{months[-1]}（分析は終了月のスナップショット）',
    }


def tool_get_services(start, end):
    """Service type breakdown."""
    ok, err = _check_range(start, end)
    if not ok:
        return {'error': err}
    months = _months_in_range(start, end)
    per_month = [_store.get(f'{m}/services') or [] for m in months]
    merged = {}
    for rows in per_month:
        for r in rows:
            st = r.get('sales_type')
            if not st:
                continue
            if st not in merged:
                merged[st] = copy.deepcopy(r)
            else:
                for k in ('total',):
                    merged[st][k] = (merged[st].get(k) or 0) + (r.get(k) or 0)
    return sorted(merged.values(), key=lambda r: -(r.get('total') or 0))[:15]


def tool_get_persons(start, end):
    """Person list (top 20 by total)."""
    ok, err = _check_range(start, end)
    if not ok:
        return {'error': err}
    months = _months_in_range(start, end)
    end_persons = _store.get(f'{months[-1]}/persons') or []
    return [
        {'full_name': p.get('full_name', ''), 'residence': p.get('residence', ''),
         'total': p.get('total', 0), 'insurance': p.get('insurance', 0),
         'self_pay': p.get('self_pay', 0), 'service_count': p.get('service_count', 0)}
        for p in sorted(end_persons, key=lambda x: -(x.get('total') or 0))[:20]
    ]


def tool_get_haifu(start, end):
    """Allocation (haifu) data."""
    ok, err = _check_range(start, end)
    if not ok:
        return {'error': err}
    months = _months_in_range(start, end)
    end_data = _store.get(f'{months[-1]}/haifu') or []
    return [
        {'name': f['name'], 'native_total': f.get('native_total', 0),
         'haifu_in_total': f.get('haifu_in_total', 0),
         'haifu_out_total': f.get('haifu_out_total', 0),
         'total': f.get('total', 0)}
        for f in sorted(end_data, key=lambda x: -(abs(x.get('total', 0))))[:15]
    ]


def tool_get_map(start, end):
    """Facility geographic distribution: locations, regions, revenue per facility."""
    ok, err = _check_range(start, end)
    if not ok:
        return {'error': err}
    months = _months_in_range(start, end)
    pins = _store.get(f'{months[-1]}/map') or []
    return [
        {'name': p['office_name'], 'region': p.get('region', ''),
         'type': p.get('type', ''), 'revenue': p.get('revenue', 0),
         'active': p.get('active', 0), 'address': p.get('address', '')}
        for p in sorted(pins, key=lambda x: -(x.get('revenue') or 0))
    ]

def tool_get_facility_detail(name, start, end):
    """Single facility drill-down."""
    ok, err = _check_range(start, end)
    if not ok:
        return {'error': err}
    months = _months_in_range(start, end)
    details = _store.get(f'{months[-1]}/facility_details') or {}
    fd = details.get(name)
    if not fd:
        # Try fuzzy match
        for k in details:
            if name in k or k in name:
                fd = details[k]
                break
    if not fd:
        return {'error': f'拠点「{name}」が見つかりません。'}
    return {
        'name': name,
        'billing': fd.get('billing', [])[:10],
        'residents_count': len(fd.get('residents', [])),
        'users_count': len(fd.get('users', [])),
    }


def tool_get_person_detail(person_id, start, end):
    """Single person detail."""
    ok, err = _check_range(start, end)
    if not ok:
        return {'error': err}
    months = _months_in_range(start, end)
    pd = _store.get(f'{months[-1]}/person_details') or {}
    p = pd.get(str(person_id))
    if not p:
        return {'error': f'利用者 ID「{person_id}」が見つかりません。'}
    info = p.get('info', {})
    bills = p.get('bills', [])
    util = p.get('utilization', {})
    return {
        'person_id': person_id,
        'full_name': info.get('full_name', ''),
        'residence': info.get('residence', ''),
        'customer_type': info.get('customer_type', ''),
        'bills': [{'sales_type': b.get('sales_type', ''), 'amount': b.get('amount', 0),
                    'insurance': b.get('insurance', 0), 'self_pay': b.get('self_pay', 0)}
                  for b in bills[:15]],
        'utilization': util,
    }


# ── Tool definitions (OpenAI function-calling format) ─────────────────────────

TOOLS = [
    {
        'type': 'function',
        'function': {
            'name': 'get_kpi',
            'description': 'Get monthly KPI data (total revenue, insurance, self-pay, customers, facilities) for a date range.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'start': {'type': 'string', 'description': 'Start month (YYYY-MM), e.g. 2025-04'},
                    'end': {'type': 'string', 'description': 'End month (YYYY-MM), e.g. 2026-03'},
                },
                'required': ['start', 'end'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_facilities',
            'description': 'Get per-facility billing data (revenue, insurance, self-pay, categories) for a date range.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'start': {'type': 'string', 'description': 'Start month (YYYY-MM)'},
                    'end': {'type': 'string', 'description': 'End month (YYYY-MM)'},
                },
                'required': ['start', 'end'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_analysis',
            'description': 'Get analysis data: care level distribution, insurance utilization, pipeline, tenure for a date range (end-month snapshot).',
            'parameters': {
                'type': 'object',
                'properties': {
                    'start': {'type': 'string', 'description': 'Start month (YYYY-MM)'},
                    'end': {'type': 'string', 'description': 'End month (YYYY-MM)'},
                },
                'required': ['start', 'end'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_services',
            'description': 'Get service type breakdown (revenue by sales_type) for a date range.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'start': {'type': 'string', 'description': 'Start month (YYYY-MM)'},
                    'end': {'type': 'string', 'description': 'End month (YYYY-MM)'},
                },
                'required': ['start', 'end'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_persons',
            'description': 'Get top 20 persons by total spending for a date range.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'start': {'type': 'string', 'description': 'Start month (YYYY-MM)'},
                    'end': {'type': 'string', 'description': 'End month (YYYY-MM)'},
                },
                'required': ['start', 'end'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_haifu',
            'description': 'Get internal allocation (haifu) data: native, inflow, outflow per facility.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'start': {'type': 'string', 'description': 'Start month (YYYY-MM)'},
                    'end': {'type': 'string', 'description': 'End month (YYYY-MM)'},
                },
                'required': ['start', 'end'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_map',
            'description': 'Get facility geographic distribution: locations, regions, types, revenue per facility. Use for questions about geographic spread, regional composition, or facility locations.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'start': {'type': 'string', 'description': 'Start month (YYYY-MM)'},
                    'end': {'type': 'string', 'description': 'End month (YYYY-MM)'},
                },
                'required': ['start', 'end'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_facility_detail',
            'description': 'Get detail for a specific facility: billing, residents, users.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'name': {'type': 'string', 'description': 'Facility name, e.g. 桜木拠点'},
                    'start': {'type': 'string', 'description': 'Start month (YYYY-MM)'},
                    'end': {'type': 'string', 'description': 'End month (YYYY-MM)'},
                },
                'required': ['name', 'start', 'end'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_person_detail',
            'description': 'Get detail for a specific person: bills, services, insurance utilization.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'id': {'type': 'string', 'description': 'Person ID (number)'},
                    'start': {'type': 'string', 'description': 'Start month (YYYY-MM)'},
                    'end': {'type': 'string', 'description': 'End month (YYYY-MM)'},
                },
                'required': ['id', 'start', 'end'],
            },
        },
    },
]

TOOL_MAP = {
    'get_kpi': tool_get_kpi,
    'get_facilities': tool_get_facilities,
    'get_analysis': tool_get_analysis,
    'get_services': tool_get_services,
    'get_persons': tool_get_persons,
    'get_haifu': tool_get_haifu,
    'get_map': tool_get_map,
    'get_facility_detail': tool_get_facility_detail,
    'get_person_detail': tool_get_person_detail,
}


# ── Chat stream ───────────────────────────────────────────────────────────────

def chat_stream(messages):
    """Yield NDJSON events: {type: 'chunk', text: ...} | {type: 'tool_call', name: ..., args: ...} | {type: 'done'} | {type: 'error', message: ...}.

    Supports multi-turn: messages is the full conversation history.
    Handles up to 3 rounds of tool calls.
    """
    # Build message list, stripping ALL tool messages — the client doesn't
    # preserve tool_calls in assistant messages, so tool messages are always
    # orphaned across turns. Tool calls are re-issued fresh each turn.
    msgs = [{'role': 'system', 'content': SYSTEM_PROMPT}]
    for m in messages:
        if m.get('role') == 'tool':
            continue  # never include tool messages from client history
        msgs.append(m)
    max_rounds = 3

    for _round in range(max_rounds):
        stream = _client.chat.completions.create(
            model=CHAT_MODEL,
            messages=msgs,
            tools=TOOLS,
            tool_choice='auto',
            max_completion_tokens=4000,
            stream=True,
        )

        # Collect streaming response
        content_chunks = []
        reasoning_chunks = []
        tool_calls = {}  # index -> {id, name, args_chunks}
        finish_reason = None

        for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta is None:
                continue

            # Reasoning content (thinking mode — must be passed back)
            if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                reasoning_chunks.append(delta.reasoning_content)

            # Text content
            if delta.content:
                content_chunks.append(delta.content)
                yield _ndjson({'type': 'chunk', 'text': delta.content})

            # Tool calls
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_calls:
                        tool_calls[idx] = {'id': tc.id or '', 'name': '', 'args': ''}
                    if tc.id:
                        tool_calls[idx]['id'] = tc.id
                    if tc.function:
                        if tc.function.name:
                            tool_calls[idx]['name'] = tc.function.name
                        if tc.function.arguments:
                            tool_calls[idx]['args'] += tc.function.arguments

            if chunk.choices[0].finish_reason:
                finish_reason = chunk.choices[0].finish_reason

        # If the model called tools, execute them and continue
        if tool_calls and finish_reason == 'tool_calls':
            # Build assistant message with tool_calls FIRST
            content_text = ''.join(content_chunks).strip()
            reasoning_text = ''.join(reasoning_chunks).strip()
            assistant_msg = {'role': 'assistant'}
            if reasoning_text:
                assistant_msg['reasoning_content'] = reasoning_text
            if content_text:
                assistant_msg['content'] = content_text
            assistant_tool_calls = []
            for idx in sorted(tool_calls.keys()):
                tc = tool_calls[idx]
                yield _ndjson({'type': 'tool_call', 'name': tc['name'], 'args': tc['args']})
                assistant_tool_calls.append({
                    'id': tc['id'],
                    'type': 'function',
                    'function': {'name': tc['name'], 'arguments': tc['args']},
                })
            assistant_msg['tool_calls'] = assistant_tool_calls
            msgs.append(assistant_msg)  # assistant BEFORE tool results

            # Now execute tools and add results AFTER assistant
            for idx in sorted(tool_calls.keys()):
                tc = tool_calls[idx]
                try:
                    args = json.loads(tc['args'])
                except json.JSONDecodeError:
                    args = {}
                fn = TOOL_MAP.get(tc['name'])
                if fn:
                    result = fn(**args)
                else:
                    result = {'error': f'Unknown tool: {tc["name"]}'}
                msgs.append({
                    'role': 'tool',
                    'tool_call_id': tc['id'],
                    'content': json.dumps(result, ensure_ascii=False),
                })
            continue  # next round

        # No tool calls or final response
        yield _ndjson({'type': 'done'})
        return

    yield _ndjson({'type': 'done'})
    yield _ndjson({'type': 'chunk', 'text': '\n\n（ツール呼び出しの上限に達しました）'})


def _ndjson(obj):
    return json.dumps(obj, ensure_ascii=False) + '\n'
