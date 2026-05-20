"""Run 10 rounds of varied chat questions and report pass/fail."""
from gcs_store import GCSDataStore
import ai_chat, json

store = GCSDataStore()
store.load_from_local('data')
ai_chat.init(store)

questions = [
    '配賦の仕組みを教えて',
    '今月の売上トップ3拠点は？',
    'このダッシュボードの技術スタックを詳しく教えて',
    'データはどうやって生成された？',
    '介護度分布はどのように推定している？',
    '拠点（配賦）タブと拠点タブの違いは？',
    '一番売上が大きいサービスは？',
    '前月と比べて変化が大きい拠点は？',
    'なぜ配賦が必要なのか？',
    'このダッシュボードのアーキテクチャを説明して',
]

results = []
for i, q in enumerate(questions):
    print(f'\n[{i+1}/10] {q}')
    msgs = [{'role': 'user', 'content': q}]
    text = ''; has_sug = False; error = None; tools = 0
    for line in ai_chat.chat_stream(msgs):
        try:
            d = json.loads(line)
        except:
            continue
        if d['type'] == 'chunk': text += d['text']
        elif d['type'] == 'tool_call': tools += 1
        elif d['type'] == 'error': error = d['message']; break
        elif d['type'] == 'done':
            has_sug = '[SUGGESTIONS]' in text
            break
    if error:
        print(f'  FAIL: {error[:120]}')
        results.append(('FAIL', q, error[:80]))
    else:
        status = f'OK ({len(text)} chars, {tools} tools, sug={has_sug})'
        print(f'  {status}')
        results.append(('OK', q, ''))

print('\n' + '='*50)
ok = sum(1 for r in results if r[0] == 'OK')
print(f'Results: {ok}/{len(results)} passed')
for r in results:
    print(f'  [{r[0]}] {r[1]}')
    if r[2]: print(f'       -> {r[2]}')
