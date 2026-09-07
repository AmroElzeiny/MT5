# Full AI Run — GOLD M15

الحالة الجاهزة:

- AI memory: 81 صفقة CLEAN.
- Accepted: `micro_continuation_fvg` (30)، `micro_bisi_sibi_edge` (24)، `micro_range_reentry` (21).
- Blocked: `micro_po3_reversal` (6) وأي family بدون عينة.
- الـ priors إلزامية وصالحة: schema `20260717_hierarchical_prior_v1`.
- ممنوع استخدام live wait داخل Strategy Tester؛ الاختبار الكامل Record ثم Cache ثم Replay.
- Record-only بيصدر كل tester-cache signature مرة واحدة؛ smoke ثابت خفّض 301 request إلى 29 unique requests.

## 1. Record-only run

شغّل:

```powershell
& "C:\Program Files\FxPro - MetaTrader 5\terminal64.exe" "/config:C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\PO3_Codex\MT5\autonomous_ai_browser_bridge\logs\full_ai_stage1_record_nonvisual.ini"
```

المرحلة دي بتجمع كل طلبات AI ولا تفتح صفقات.

## 2. Fill tester cache

شغّل الـ browser provider في نافذة مستقلة:

```powershell
& "C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\PO3_Codex\MT5\autonomous_ai_browser_bridge\run_bridge.ps1"
```

بعد ما الـ dashboard يبقى شغال، نفّذ:

```powershell
Set-Location "C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\PO3_Codex\MT5\python"
python ai_gate.py --fill-tester-cache-once
```

لا تبدأ الـReplay إلا لما `requests=0` وكل قرارات Stage 1 تكون موجودة في `PO3_AI_BUS\logs\tester_ai_cache`.

## 3. Cache replay full backtest

شغّل:

```powershell
& "C:\Program Files\FxPro - MetaTrader 5\terminal64.exe" "/config:C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\PO3_Codex\MT5\autonomous_ai_browser_bridge\logs\full_ai_stage2_cache_replay_nonvisual.ini"
```

دي مرحلة الاختبار الفعلية: قرارات AI ثابتة بالـsignature، الزمن المحاكى لا يقفز، والصفقات المقبولة فقط هي اللي تتنفذ.
