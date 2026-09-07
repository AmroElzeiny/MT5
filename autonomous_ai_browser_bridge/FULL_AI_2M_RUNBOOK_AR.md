# اختبار Full AI المختصر — GOLD M15

النسخة دي بتختبر الفترة من 27 مايو إلى 27 يوليو 2026، وبتستخدم `HYBRID` علشان مسارات
`micro_continuation_fvg` و`micro_bisi_sibi_edge` و`micro_range_reentry` تتولد فعلًا.
العائلات ضعيفة العينة ما زالت مقفولة بحدود `999` داخل ملفي الـset.

## 1. Stage 1 — تسجيل الطلبات فقط

```powershell
& "C:\Program Files\FxPro - MetaTrader 5\terminal64.exe" "/config:C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\PO3_Codex\MT5\autonomous_ai_browser_bridge\logs\full_ai_2m_stage1_record_nonvisual.ini"
```

بعد انتهاء الاختبار، اعرض خطة العينة من غير استدعاء AI ومن غير نقل أي ملفات:

```powershell
& "C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\PO3_Codex\MT5\autonomous_ai_browser_bridge\plan_full_ai_2m_cache.ps1"
```

المستهدف بحد أقصى 60 request: عشرين لكل primary family. الاختيار deterministic ومتوازن،
وموزع على الرن باستخدام hash بدل أخذ أول أيام فقط. لو إحدى العائلات لم تظهر، الأمر يعالج المتاح
ولا يملأ مكانها بشكل مصطنع.

## 2. ملء الجزء المحدود من الـcache

شغّل الـbrowser bridge في نافذة مستقلة:

```powershell
& "C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\PO3_Codex\MT5\autonomous_ai_browser_bridge\run_bridge.ps1"
```

ثم شغّل:

```powershell
& "C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\PO3_Codex\MT5\autonomous_ai_browser_bridge\fill_full_ai_2m_cache.ps1"
```

الأمر قابل للاستكمال: القرارات المكتملة تظل في `logs\tester_ai_cache`، والطلبات غير المختارة
تظل في `requests`. تشغيله مرة ثانية يختار دفعة جديدة من المتبقي إذا احتجنا توسيع العينة.

## 3. Stage 2 — Cache replay

```powershell
& "C:\Program Files\FxPro - MetaTrader 5\terminal64.exe" "/config:C:\Users\amroe\Downloads\NovaAIS_Systems\Trading\PO3_Codex\MT5\autonomous_ai_browser_bridge\logs\full_ai_2m_stage2_cache_replay_nonvisual.ini"
```

الطلبات التي لها قرار AI صالح في الـcache يمكنها المتابعة. أي cache miss يفشل مغلقًا ومن غير
انتظار browser، لذلك الزمن المحاكى لا يقفز والاختبار لا يتجمد.
