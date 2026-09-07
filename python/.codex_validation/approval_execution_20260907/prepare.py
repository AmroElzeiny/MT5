from pathlib import Path
import configparser
import json
import shutil

ROOT = Path(__file__).resolve().parent
SOURCE = Path(r'C:\Users\amroe\AppData\Roaming\MetaQuotes\Terminal\0148BD5691B65B0F2157627A4231F3DE')
COMMON = SOURCE.parent / 'Common' / 'Files'
BUS = COMMON / 'PO3_APPROVAL_AUDIT_20260907'
TERMINAL = ROOT / 'terminal'
PRESET = SOURCE / 'MQL5/profiles/Tester/PO3_AIGate_ScannerEA.#USNDAQ100.M15.20260803_20260808.100.ini'


def read_text(path):
    raw = path.read_bytes()
    return raw.decode('utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig')


def main():
    TERMINAL.mkdir(parents=True, exist_ok=True)
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.optionxform = str
    cfg.read_string(read_text(PRESET))
    symbols = cfg['TesterInputs']['InpTesterSymbols'].split(',')
    for name in ('terminal64.exe', 'metatester64.exe', 'MetaEditor64.exe'):
        shutil.copy2(Path(r'C:\Program Files\FxPro - MetaTrader 5') / name, TERMINAL / name)
    for name in ('accounts.dat', 'servers.dat'):
        target = TERMINAL / 'config' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SOURCE / 'config' / name, target)
    base = SOURCE / 'bases/FxPro-MT5 Demo'
    shutil.copytree(base / 'symbols', TERMINAL / 'bases/FxPro-MT5 Demo/symbols', dirs_exist_ok=True)
    for symbol in symbols:
        dest = TERMINAL / 'bases/FxPro-MT5 Demo/history' / symbol
        dest.mkdir(parents=True, exist_ok=True)
        for year in ('2025.hcc', '2026.hcc'):
            source = base / 'history' / symbol / year
            if source.exists():
                try:
                    shutil.copy2(source, dest / year)
                except PermissionError:
                    print(f'History locked by active terminal; isolated terminal will download {symbol}/{year}')
    for folder in ('Include/MT5_PO3_Codex', 'Include/Trade', 'Include/Arrays', 'Include/Generic', 'Include/Math', 'Experts/MT5_PO3_Codex'):
        source = SOURCE / 'MQL5' / folder
        if source.exists():
            shutil.copytree(source, TERMINAL / 'MQL5' / folder, dirs_exist_ok=True)
    # Policies remain at their original read-only paths: paths themselves participate
    # in the economic input hash. Only transport/output is isolated.
    shutil.copytree(COMMON / 'PO3_AI_BUS/config', BUS / 'config', dirs_exist_ok=True)
    for name in ('requests', 'responses', 'logs', 'processing', 'locks'):
        (BUS / name).mkdir(parents=True, exist_ok=True)
    cfg['Common'] = {'Login': '591813800', 'Server': 'FxPro-MT5 Demo', 'KeepPrivate': '1'}
    cfg['Experts'] = {'Enabled': '0', 'AllowLiveTrading': '0', 'AllowDllImport': '0'}
    cfg['Tester'].update({'Login': '591813800', 'UseLocal': '1', 'UseRemote': '0', 'UseCloud': '0', 'ShutdownTerminal': '1', 'ReplaceReport': '1'})
    cfg['TesterInputs']['InpBusRoot'] = BUS.name
    cfg['TesterInputs']['InpTesterAiMode'] = '0||0||0||3||N'
    cfg['Tester']['Report'] = 'record_report'
    for mode in ('record', 'replay'):
        if mode == 'replay':
            cfg['TesterInputs']['InpTesterAiMode'] = '1||0||0||3||N'
            cfg['Tester']['Report'] = 'replay_report'
        with (ROOT / f'{mode}.ini').open('w', encoding='utf-16') as handle:
            cfg.write(handle, space_around_delimiters=False)
    (ROOT / 'environment.json').write_text(json.dumps({'terminal': str(TERMINAL), 'bus': str(BUS), 'preset': str(PRESET), 'symbols': symbols}, indent=2))
    print(json.dumps({'ready': True, 'symbols': len(symbols), 'terminal': str(TERMINAL), 'bus': str(BUS)}))


if __name__ == '__main__':
    main()
