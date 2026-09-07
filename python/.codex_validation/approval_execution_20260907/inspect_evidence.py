from pathlib import Path
from collections import Counter
import json

ROOT = Path(__file__).resolve().parent
BUS = Path(r'C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\Common\Files\PO3_AI_BUS')


def read(path):
    raw = path.read_bytes()
    return json.loads(raw.decode('utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'))


def main():
    approved = {}
    states = Counter()
    for path in (BUS / 'logs/tester_ai_cache').glob('*.json'):
        data = read(path)
        states[data.get('decision_state')] += 1
        if data.get('python_final_allow'):
            req = data.get('id')
            approved.setdefault(req, []).append((path, data))
    print('states', states, 'approved_unique', len(approved))
    details = []
    for req, values in sorted(approved.items()):
        path, data = values[0]
        entry, sl, tp1 = (data.get(k, 0) for k in ('assessed_entry', 'assessed_sl', 'assessed_tp1'))
        item = {k: data.get(k) for k in ('request_id', 'selected_candidate_hash', 'request_execution_fingerprint', 'assessed_execution_fingerprint', 'assessed_entry', 'assessed_sl', 'assessed_tp1', 'assessed_tp2', 'chosen_target_model', 'chosen_tp1', 'chosen_tp2')}
        item.update(cache=str(path), artifacts=len(values), tp1_r=abs(tp1-entry)/abs(entry-sl) if entry != sl else 0)
        item['request_id'] = req
        details.append(item)
        print(json.dumps(item))
    (ROOT / 'original_approvals.json').write_text(json.dumps(details, indent=2))
    for path in (BUS / 'config').glob('*policy*.json'):
        raw = path.read_bytes()
        for method, s in [('ascii', raw.decode('utf-8')), ('utf16_drop', raw[:len(raw)//2*2].decode('utf-16-le')), ('utf16_pad', (raw+b'\x00' if len(raw)%2 else raw).decode('utf-16-le'))]:
            h = 2166136261
            for char in s:
                h = ((h ^ ord(char))*16777619)&0xffffffff
            print(path.name, len(raw), method, h % 2147483647)


if __name__ == '__main__':
    main()
