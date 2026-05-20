"""Quick test for ai_chat module."""
from gcs_store import GCSDataStore
import ai_chat, json

store = GCSDataStore()
store.load_from_local('data')
ai_chat.init(store)

tests = [
    'こんにちは',
    '配賦の仕組みを教えて',
    '今月の売上トップ3拠点は？',
]

for q in tests:
    print(f'\n=== TEST: {q} ===')
    msgs = [{'role': 'user', 'content': q}]
    tool_count = 0
    chunk_count = 0
    error = None
    for line in ai_chat.chat_stream(msgs):
        d = json.loads(line)
        if d['type'] == 'chunk':
            chunk_count += 1
        elif d['type'] == 'tool_call':
            tool_count += 1
        elif d['type'] == 'error':
            error = d['message']
            break
        elif d['type'] == 'done':
            break
    if error:
        print(f'  FAIL: {error[:150]}')
    else:
        print(f'  OK: {chunk_count} chunks, {tool_count} tool calls')

# Test multi-turn
print('\n=== TEST: Multi-turn ===')
msgs = [{'role': 'user', 'content': '今月の売上は？'}]
r1_ok = True
for line in ai_chat.chat_stream(msgs):
    d = json.loads(line)
    if d['type'] == 'error':
        r1_ok = False
        print(f'  Turn1 FAIL: {d["message"][:80]}')
        break
if r1_ok:
    print('  Turn1: OK')
    msgs.append({'role': 'assistant', 'content': 'ok'})
    msgs.append({'role': 'user', 'content': '前月と比べてどう？'})
    for line in ai_chat.chat_stream(msgs):
        d = json.loads(line)
        if d['type'] == 'error':
            print(f'  Turn2 FAIL: {d["message"][:80]}')
            break
        if d['type'] == 'done':
            print('  Turn2: OK')
            break

print('\nAll tests done.')
