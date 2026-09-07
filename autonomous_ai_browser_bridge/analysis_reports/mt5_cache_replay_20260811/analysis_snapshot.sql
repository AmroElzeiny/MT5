-- Reproducible local snapshot for the MT5 cache-replay report.
-- Raw evidence paths and read-only extraction notes are listed in source_notes.md.

CREATE TABLE headline_metrics(metric TEXT PRIMARY KEY, value REAL NOT NULL);
INSERT INTO headline_metrics(metric, value) VALUES
  ('Executed trades', 0),
  ('Valid tester-cache decisions', 27),
  ('Recorded-request coverage', 27.0 / 3715.0),
  ('AI approvals', 1);

CREATE TABLE run_facts(metric TEXT PRIMARY KEY, value TEXT NOT NULL, interpretation TEXT NOT NULL);
INSERT INTO run_facts(metric, value, interpretation) VALUES
  ('Tester result', 'Passed in 14m 44.814s', 'The run completed without a technical crash.'),
  ('History quality', '100%', 'The test data was internally complete for the selected run.'),
  ('Bars / ticks', '3,924 / 235,381', 'The selected two-month GOLD M15 period was processed.'),
  ('Initial / final balance', '$10,000 / $10,000', 'No trading P&L occurred.'),
  ('Trades / deals', '0 / 0', 'No order reached an executed position.'),
  ('Tester mode', 'TESTER_AI_CACHE_ONLY', 'Only exact cached AI decisions were eligible; no browser wait was used.');

CREATE TABLE decision_outcomes(decision TEXT PRIMARY KEY, count INTEGER NOT NULL, share REAL NOT NULL, meaning TEXT NOT NULL);
INSERT INTO decision_outcomes(decision, count, share, meaning) VALUES
  ('REJECT', 20, 20.0 / 27.0, 'Qualitative block or veto prevented capital commitment.'),
  ('ABSTAIN', 6, 6.0 / 27.0, 'The evidence did not resolve to an approval.'),
  ('APPROVE', 1, 1.0 / 27.0, 'One cached plan was eligible for downstream MT5 validation.');

CREATE TABLE family_mix(family TEXT PRIMARY KEY, count INTEGER NOT NULL, share REAL NOT NULL);
INSERT INTO family_mix(family, count, share) VALUES
  ('Continuation FVG', 12, 12.0 / 27.0),
  ('Breaker retest', 6, 6.0 / 27.0),
  ('FVG-edge reversal', 4, 4.0 / 27.0),
  ('Range re-entry', 4, 4.0 / 27.0),
  ('Nested continuation', 1, 1.0 / 27.0);

CREATE TABLE readiness(layer TEXT PRIMARY KEY, status TEXT NOT NULL, next_step TEXT NOT NULL);
INSERT INTO readiness(layer, status, next_step) VALUES
  ('Tester stability', 'Ready', 'No immediate MT5 stability blocker.'),
  ('AI historical context', 'Ready for testing', 'The bootstrap loop is resolved.'),
  ('Cache replay coverage', 'Not ready', 'Do not use this run for performance conclusions.'),
  ('Approval-to-execution trace', 'Unknown', 'Run the 10–11 June diagnostic.'),
  ('Live readiness', 'Not ready', 'Require staged smoke and practical validation.');

-- Integrity checks used before report packaging.
SELECT 'decision_count' AS check_name, SUM(count) AS observed, 27 AS expected FROM decision_outcomes;
SELECT 'family_count' AS check_name, SUM(count) AS observed, 27 AS expected FROM family_mix;
SELECT 'cache_coverage' AS check_name, value AS observed, 27.0 / 3715.0 AS expected
FROM headline_metrics WHERE metric = 'Recorded-request coverage';
