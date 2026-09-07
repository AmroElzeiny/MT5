from pathlib import Path
import argparse
import json
import re
from collections import Counter

ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument('--since', default='02:09:00')
parser.add_argument('--name', default='baseline')
parser.add_argument('--log', type=Path, default=ROOT/'terminal/Tester/logs/20260907.log')
args = parser.parse_args()
counts = Counter()
approvals = []
summaries = []
important = re.compile(r'\[decision_identity\]|\[tester_ai_cache_cohort\]|\[startup_reject\]|AI advisory.*(?:raw_allow=true|final_allow=true|candidate_hash_match=false)|\[assessed_leg_below_floor\]|\[assessed_plan_identity\]|\[execution_fingerprint\]|\[execution_adjustment|\[obstacle_identity_gate\]|\[deterministic_rr_target_cap\]|\[execution_identity\]|\[order_adapter|\[broker_result|\[trade_attribution|\[tester_replay_binding\]|watchlist.*(?:added|reject|invalidat|expired)|\[tp1_authority\].*reject|\[final_summary\]|deal performed|order performed|position.*(?:opened|closed)|final balance|Test passed|\[execution_lineage\]|\[trade_lineage\]')
with args.log.open(encoding='utf-16', errors='replace') as handle, (ROOT/f'{args.name}_events.log').open('w', encoding='utf-8') as output:
    for line in handle:
        parts = line.split('\t', 4)
        if len(parts) < 5 or parts[2][:8] < args.since:
            continue
        msg = parts[4].rstrip()
        if 'reject_reason=' in msg:
            match = re.search(r'reject_reason=([^\s]+)', msg)
            if match:
                counts[match[1]] += 1
        if 'AI advisory' in msg and 'raw_allow=true' in msg:
            approvals.append(msg)
        if '[final_summary]' in msg or '[decision_identity]' in msg or '[tester_ai_cache_cohort]' in msg:
            summaries.append(msg)
        if important.search(msg):
            output.write(msg+'\n')
report = dict(rejects=counts, approvals=approvals, summaries=summaries)
(ROOT/f'{args.name}_audit.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps({'rejects': counts.most_common(15), 'approvals': len(approvals), 'summary': summaries[-15:]}, indent=2))
