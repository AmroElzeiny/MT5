from pathlib import Path
import configparser

from prepare import ROOT, read_text


def write_config(
    name: str,
    from_date: str,
    to_date: str,
    report: str,
    *,
    tester_ai_mode: str = "1||0||0||3||N",
    bus_root: str = "PO3_APPROVAL_AUDIT_20260907",
) -> None:
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.optionxform = str
    cfg.read_string(read_text(ROOT / "replay.ini"))
    cfg["Tester"]["FromDate"] = from_date
    cfg["Tester"]["ToDate"] = to_date
    cfg["Tester"]["Report"] = report
    cfg["TesterInputs"]["InpTesterAiMode"] = tester_ai_mode
    cfg["TesterInputs"]["InpBusRoot"] = bus_root
    for legacy_key in (
        "InpTesterLegacyPolicyFingerprint",
        "InpTesterLegacyNormalizedPolicyRawHash",
        "InpTesterLegacyInvalidationPolicyRawHash",
    ):
        cfg["TesterInputs"].pop(legacy_key, None)
    with (ROOT / name).open("w", encoding="utf-16") as handle:
        cfg.write(handle, space_around_delimiters=False)


write_config(
    "current_targeted_replay.ini",
    "2026.08.06",
    "2026.08.07",
    "current_openrouter_approval_replay",
)
write_config(
    "current_full_replay.ini",
    "2026.08.03",
    "2026.08.08",
    "current_openrouter_full_replay",
)
write_config(
    "final_current_record.ini",
    "2026.08.06",
    "2026.08.07",
    "final_current_record",
    tester_ai_mode="0||0||0||3||N",
    bus_root="PO3_APPROVAL_AUDIT_FINAL_20260907",
)
write_config(
    "final_current_replay.ini",
    "2026.08.06",
    "2026.08.07",
    "final_current_openrouter_replay",
    bus_root="PO3_APPROVAL_AUDIT_FINAL_20260907",
)
print("Current replay configurations written.")
