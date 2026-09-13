import json
import sys
from pathlib import Path

if len(sys.argv) != 3:
    print("usage: validate-report.py REPORT.json SCHEMA.json", file=sys.stderr)
    raise SystemExit(2)

report_path = Path(sys.argv[1])
schema_path = Path(sys.argv[2])

try:
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    schema = json.loads(schema_path.read_text(encoding="utf-8-sig"))
except Exception as exc:
    print(f"invalid json: {exc}", file=sys.stderr)
    raise SystemExit(3)

errors = []
for key in schema.get("required", []):
    if key not in report:
        errors.append(f"missing required field: {key}")

props = schema.get("properties", {})
for field in ("front_end","tier","mode","status","final_verdict"):
    if field in report and "enum" in props.get(field, {}):
        if report[field] not in props[field]["enum"]:
            errors.append(f"invalid {field}: {report[field]}")

reqs = report.get("requirements")
if not isinstance(reqs, list) or not reqs:
    errors.append("requirements must be a non-empty array")
else:
    ids = []
    for i, item in enumerate(reqs):
        if not isinstance(item, dict):
            errors.append(f"requirements[{i}] must be object")
            continue
        rid = item.get("id")
        status = item.get("status")
        if not rid:
            errors.append(f"requirements[{i}] missing id")
        if status not in {"PASS","BLOCKED","ESCALATED"}:
            errors.append(f"requirements[{i}] invalid status")
        ids.append(rid)
    if len(ids) != len(set(ids)):
        errors.append("duplicate requirement IDs")

if report.get("mode") == "Offline" and report.get("live_action_audit"):
    errors.append("Offline run must have empty live_action_audit")

if errors:
    for e in errors:
        print("ERROR:", e, file=sys.stderr)
    raise SystemExit(4)

print("VALID")
