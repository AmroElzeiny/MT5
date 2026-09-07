from prepare import ROOT, COMMON, BUS, TERMINAL, read_text
import configparser
import shutil

cfg = configparser.ConfigParser(interpolation=None)
cfg.optionxform = str
cfg.read_string(read_text(ROOT / 'replay.ini'))
cfg['TesterInputs'].update({
    'InpTesterLegacyPolicyFingerprint': 'true',
    'InpTesterLegacyNormalizedPolicyRawHash': '1441715152',
    'InpTesterLegacyInvalidationPolicyRawHash': '640752513',
})
cfg['Tester']['Report'] = 'legacy_replay_baseline'
shutil.copytree(COMMON / 'PO3_AI_BUS/logs/tester_ai_cache', BUS / 'logs/tester_ai_cache', dirs_exist_ok=True)
with (ROOT / 'legacy_replay.ini').open('w', encoding='utf-16') as handle:
    cfg.write(handle, space_around_delimiters=False)
print('Historical cache copied without changing any decision or identity.')
