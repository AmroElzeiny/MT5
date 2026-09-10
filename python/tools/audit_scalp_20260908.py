"""Read-only audit of the September 8 scalp session. Writes only audit artifacts."""
import collections
import csv
import datetime as dt
import json
import os
from pathlib import Path
import re

BUS = Path(os.environ['APPDATA']) / 'MetaQuotes/Terminal/Common/Files/PO3_AI_BUS'
TERMINAL = BUS.parents[2] / '0148BD5691B65B0F2157627A4231F3DE'
SESSION = '591813800_1788837043_120154281'
OUT = Path(__file__).resolve().parents[1] / 'docs/audit_scalp_20260908'
OUT.mkdir(parents=True, exist_ok=True)

def read(p):
    raw = p.read_bytes()
    return raw.decode('utf-16' if raw[:2] in (b'\xff\xfe', b'\xfe\xff') else 'utf-8-sig')

def counter(rows, key):
    return dict(collections.Counter(str(r.get(key)) for r in rows))

docs = [json.loads(read(p)) for p in (BUS/'response_debug').glob(SESSION+'*.json')]
responses = [d['response'] for d in docs]
assessments = []
for r in responses:
    for a in r.get('candidate_assessments', []):
        raw_a = r.get('analyst_output',{}) if r.get('analyst_output',{}).get('candidate_hash') == a.get('candidate_hash') else a
        assessments.append(dict(a, audit_request_id=r['id'], audit_final_allow=r.get('python_final_allow'), analyst_decision_state=raw_a.get('decision_state')))
requests = {}
for folder in ['completed', 'rejected', 'shutdown', 'timed_out', 'requests', 'processing']:
    for p in (BUS/folder).glob('*'+SESSION+'*.json'):
        if p.name.endswith('.meta.json'): continue
        d = json.loads(read(p))
        if 'candidates' in d: requests[d['id']] = d
summary = {'session': SESSION, 'response_count':len(responses), 'request_artifacts':len(requests),
           'candidate_assessments':len(assessments)}
for key in ['decision_state','decision_source','python_final_allow','raw_allow','model_raw_allow',
            'final_resolver_reason','repeatability_status','repeatability_required_live',
            'historical_evidence_state','response_quality','veto_code','llm_quality_threshold_passed']:
    summary[key] = counter(responses,key)
summary['rejection_codes'] = dict(collections.Counter(c for r in responses for c in r.get('rejection_codes',[])))
summary['families'] = {}
for family in sorted(set(a.get('setup_taxonomy_enum','?') for a in assessments)):
    group = [a for a in assessments if a.get('setup_taxonomy_enum','?') == family]
    summary['families'][family] = {'n':len(group),'states':counter(group,'decision_state'),
        'history':counter(group,'historical_evidence_state'),
        'missing_required_evidence':sum(bool(a.get('missing_required_evidence')) for a in group),
        'codes':dict(collections.Counter(c for a in group for c in a.get('rejection_codes',[])))}
summary['selected_missing_evidence'] = sum(bool(r.get('analyst_output',{}).get('missing_required_evidence')) for r in responses)
summary['analyst_selected_states'] = dict(collections.Counter(r.get('analyst_output',{}).get('decision_state') for r in responses))
lost = [r for r in responses if r.get('analyst_output',{}).get('decision_state') == 'APPROVE' and not r.get('python_final_allow')]
summary['analyst_approvals_lost_in_consensus'] = len(lost)
summary['lost_approval_objections'] = dict(collections.Counter(o['code'] for r in lost for o in r.get('critic_output',{}).get('blocking_objections',[])))
summary['critic_objections'] = dict(collections.Counter(o['code'] for r in responses for o in r.get('critic_output',{}).get('blocking_objections',[])))
summary['analyst_families'] = {}
raw_assessments = []
filter_rows = []
for r in responses:
    candidates = {c['candidate_index']:c for c in requests[r['id']]['candidates']}
    selected = r.get('analyst_output',{})
    for a in r.get('candidate_assessments',[]):
        # Final binding overwrites the selected assessment verdict with consensus.
        raw_a = selected if selected.get('candidate_hash') == a.get('candidate_hash') else a
        raw_assessments.append(raw_a)
        c = candidates[a['candidate_index']]
        stale = c.get('fvg_execution_class') == 'stale_fvg' or c.get('fvg_zone_execution_class') == 'stale_fvg' or c.get('fvg_mitigation_state') in ('stale','stale_fvg')
        touched = bool(c.get('fvg_touched') or c.get('fvg_mid_mitigated') or c.get('fvg_execution_class') in ('touched_fvg','mid_mitigated_fvg','mid_mitigated_reentry') or c.get('fvg_zone_execution_class') in ('touched_fvg','mid_mitigated_fvg') or c.get('fvg_mitigation_state') in ('touched','edge_touched','mid_mitigated'))
        continuation = c.get('setup_family') in ('full_po3_continuation','micro_continuation_fvg','continuation')
        weak_touched = continuation and touched and c.get('retest_score',0)<6 and c.get('retest_quality_score',0)<6
        filter_rows.append({'request_id':r['id'],'candidate_index':a['candidate_index'], 'candidate_hash':a['candidate_hash'],
            'stale':stale,'weak_touched_continuation':weak_touched,'would_filter':stale or weak_touched,
            'analyst_state':raw_a.get('decision_state'),'final_assessment_state':a.get('decision_state'),
            'selected_final_approval':bool(r.get('python_final_allow') and r.get('selected_candidate_hash')==a['candidate_hash'])})
for family in sorted(set(a.get('setup_taxonomy_enum','?') for a in raw_assessments)):
    group = [a for a in raw_assessments if a.get('setup_taxonomy_enum','?') == family]
    summary['analyst_families'][family] = {'n':len(group),'states':counter(group,'decision_state'), 'history':counter(group,'historical_evidence_state'), 'missing_required_evidence':sum(bool(a.get('missing_required_evidence')) for a in group)}
summary['filter_projection'] = {'total':len(filter_rows), 'would_filter':sum(f['would_filter'] for f in filter_rows),
    'filtered_raw_approvals':sum(f['would_filter'] and f['analyst_state']=='APPROVE' for f in filter_rows),
    'filtered_final_approvals':sum(f['would_filter'] and f['selected_final_approval'] for f in filter_rows),
    'stale':sum(f['stale'] for f in filter_rows), 'empty_request_groups':sum(all(f['would_filter'] for f in filter_rows if f['request_id']==rid) for rid in {f['request_id'] for f in filter_rows})}
(OUT/'filter_projection.json').write_text(json.dumps(filter_rows,indent=2),encoding='utf-8')
(OUT/'lost_approvals.json').write_text(json.dumps([{k:r.get(k) for k in ['id','analyst_output','critic_output','adjudicator_output','final_resolver_reason']} for r in lost],indent=2),encoding='utf-8')
summary['approved'] = [{k:r.get(k) for k in ['id','setup_family','entry_branch','repeatability_status',
    'repeatability_required_live','historical_evidence_state','llm_quality_score','llm_quality_score_threshold',
    'llm_quality_threshold_passed','reasons','selected_candidate_id','chosen_rr2']} for r in responses if r.get('python_final_allow')]
for item in summary['approved']:
    r = next(r for r in responses if r['id']==item['id'])
    item['audited_selected_taxonomy'] = r.get('analyst_output',{}).get('setup_taxonomy_enum')
summary['distinct_candidate_ids'] = len({a['candidate_id'] for a in assessments})
usage = [json.loads(l) for l in read(BUS/'logs/openai_usage.ndjson').splitlines() if l.strip()]
usage = [u for u in usage if str(u.get('request_id','')).startswith(SESSION)]
summary['usage'] = {'rows':len(usage), 'operations':counter(usage,'operation'), 'status':counter(usage,'status'),
                    'unique_requests':len(set(u['request_id'] for u in usage)), 'models':counter(usage,'model')}
lines = read(TERMINAL/'MQL5/Logs/20260908.log').splitlines()
session_lines = [l for l in lines if 'PO3_AIGate_ScannerEA (GBPUSD,M30)' in l]
summary['journal_final'] = [l for l in session_lines if '[final_summary]' in l]
summary['journal_reasons'] = dict(collections.Counter(re.search(r'\breason=([^\s]+)',l).group(1) for l in session_lines if re.search(r'\breason=([^\s]+)',l)))
interesting = [l for l in session_lines if any(t in l for t in ['[final_summary]','[decision_authority]','[execution_authority]', 'pending order', 'watchlist add', 'watchlist remove','expired', '[decision_identity]', 'timeframes','[ai_schema_validation]'])]
(OUT/'journal_evidence.txt').write_text('\n'.join(interesting),encoding='utf-8')
for name, rows in [('responses',responses),('assessments',assessments)]:
    keys = ['id','audit_request_id','candidate_index','setup_family','setup_taxonomy_enum','entry_branch',
            'decision_state','analyst_decision_state','python_final_allow','final_resolver_reason','rejection_codes',
            'historical_evidence_state','repeatability_status','repeatability_required_live',
            'missing_required_evidence','missing_confirmations','reasons','summary','llm_quality_score']
    with (OUT/(name+'.csv')).open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=keys);w.writeheader()
        w.writerows({k: json.dumps(r.get(k),ensure_ascii=False) if isinstance(r.get(k),(list,dict)) else r.get(k) for k in keys} for r in rows)
(OUT/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
(OUT/'request_index.json').write_text(json.dumps({k:{'symbol':v.get('symbol'), 'candidates':v.get('candidates'), 'runtime_inputs':v.get('runtime_inputs')} for k,v in requests.items()}),encoding='utf-8')
print(json.dumps(summary,indent=2))
