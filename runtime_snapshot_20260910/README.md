# PO3 runtime snapshot — 2026-09-10

This directory is a point-in-time copy of PO3 runtime state that normally lives outside the Git repository.

Included sources:

- `MetaQuotes/Terminal/Common/Files/PO3_AI_BUS`
- `MetaQuotes/Terminal/Common/Files/PO3_AGENT_BUS`
- `MetaQuotes/Terminal/Common/Files/PO3_APPROVAL_AUDIT_20260907`
- `MetaQuotes/Terminal/Common/Files/PO3_APPROVAL_AUDIT_FINAL_20260907`
- Active FxPro terminal (`0148BD5691B65B0F2157627A4231F3DE`) PO3 files and journals

The copy excludes environment files and every individual file larger than 100,000,000 bytes. In particular, the live `PO3_AI_BUS/logs/shadow_candidates.jsonl` file was 142,953,865 bytes and was not copied. Other shadow/outcome ledgers and trade-history artifacts below the limit are included.

The EA and Python gate were running while this snapshot was made. A final synchronization pass was performed immediately before the Git commit.
