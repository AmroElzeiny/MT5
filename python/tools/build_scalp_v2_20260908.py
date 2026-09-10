"""Create a reviewable preset, without attaching it or changing runtime."""
import hashlib
import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'docs/audit_scalp_20260908'
PRESETS = Path(os.environ['APPDATA'])/'MetaQuotes/Terminal/0148BD5691B65B0F2157627A4231F3DE/MQL5/Profiles/Presets'
source = PRESETS/'scalp_v1.set'
raw = source.read_bytes()
text = raw.decode('utf-8-sig')
values = dict(re.findall(r'^(Inp\w+)=(.*)',text,re.M))
changes = {
    'InpScanIntervalMinutes':'10',
    'InpSuppressStaleFvgBranches':'true',
    'InpSuppressTouchedContinuationUnlessRetested':'true',
    'InpShadowCandidateLedgerEnable':'true',
}
header = '''; SCALP_V2 - EVIDENCE-BASED FORWARD-TEST CANDIDATE - 2026.09.08
; Derived from scalp_v1.set; not a proven optimum and not automatically activated.
; H1 context / M15 entries / M5 confirmation. All entry families remain enabled.
; Scan every 10 minutes, matching the observed live session cadence.
; Suppress stale gaps and touched continuation gaps lacking a clean retest.
; Historical snapshot projection: 89/533 assessments filtered, zero analyst approvals removed.
; This projection is not a strategy replay or an estimate of extra filled trades.
; Enable shadow outcome tracking for admitted candidates; do not track the full pre-AI reject flood.
; Shadow outcomes are counterfactual observations, not real fills or validated profitable samples.
; Risk, AI authority, stop/target integrity, expiry and all family switches retain v1 values.
; Pin previously omitted inputs to deployed defaults, except pre-AI shadow reject tracking is off.
; Actual previous session used pending_ai_cap=25; this preset retains v1's explicit cap of 12.
; Resolve evidence/critic inconsistencies separately; this preset cannot repair those defects.
'''
lines = [l for l in text.splitlines() if l.startswith('Inp')]
for key,new in changes.items():
    lines = [re.sub(r'^(%s=)[^|\r\n]+'%key,lambda m:m.group(1)+new,l) if l.startswith(key+'=') else l for l in lines]
config = PRESETS.parents[1]/'Include/MT5_PO3_Codex/Config.mqh'
config_text = config.read_text('utf-8-sig')
defaults = dict(re.findall(r'^input\s+\w+\s+(Inp\w+)\s*=\s*([^;]+);',config_text,re.M))
added = {}
for key,value in defaults.items():
    if key in values: continue
    value = value.strip().strip('"')
    if key == 'InpShadowTrackPreAiRejects': value = 'false'
    added[key] = value
    lines.append(key+'='+value)
result = (header+'\n'.join(lines)+'\n').replace('\n','\r\n').encode('utf-8')
parsed = dict(re.findall(r'^(Inp\w+)=(.*)',result.decode(),re.M))
assert len(parsed)==len(lines)==len(defaults)
assert set(parsed)==set(defaults)
assert all(parsed[k].split('||')[0].strip()==v for k,v in changes.items())
for key in values.keys()-changes.keys(): assert parsed[key].strip()==values[key].strip(),key
for key in ['InpEnableFvgMid','InpEnableFvgEdge','InpEnableBreakerRetest','InpEnableOteInsideFvg','InpEnableNestedFvg','InpEnableSessionReentry','InpEnableRangeReentry','InpEnableContinuationReentry']:
    assert parsed[key].split('||')[0]=='true',key
assert source.read_bytes()==raw
OUT.mkdir(parents=True,exist_ok=True)
(OUT/'scalp_v1_snapshot.set').write_bytes(raw)
for destination in [OUT/'scalp_v2.set',PRESETS/'scalp_v2.set']:
    if destination.exists() and destination.read_bytes()!=result: raise FileExistsError(destination)
    destination.write_bytes(result)
    assert destination.read_bytes()==result
verification = {'source_sha256':hashlib.sha256(raw).hexdigest(),'v2_sha256':hashlib.sha256(result).hexdigest(),
    'input_count':len(parsed),'unknown_inputs':[], 'original_unchanged':source.read_bytes()==raw,
    'changed_existing':{k:{'old':values[k].split('||')[0],'new':v} for k,v in changes.items()},
    'pinned_missing_inputs':added,'runtime_activated':False,
    'checks':['all input names exist in deployed Config.mqh','no duplicate keys','all eight branches enabled',
              'all original values outside declared changes preserved','two output copies byte-identical','source unchanged']}
(OUT/'preset_verification.json').write_text(json.dumps(verification,indent=2),encoding='utf-8')
print(json.dumps(verification,indent=2))
