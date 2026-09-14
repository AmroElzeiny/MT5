"""Harness hardening after the 2026-09-13 run: usage-limit stop/resume, .env-proof isolation, A3 clean agreement."""
import os
import py_compile
import sys
import tempfile
from pathlib import Path

REPO_PY = Path(__file__).resolve().parents[4] / "python"

H = [
    (
        "HIGH_ARMS = (\"muse_high_a\", \"muse_high_b\")\n",
        "HIGH_ARMS = (\"muse_high_a\", \"muse_high_b\")\n"
        "# Addendum A3: minimum number of pairs with no infrastructure outcome on either side\n"
        "# before a clean-agreement non-inferiority verdict may be issued.\n"
        "A3_MIN_CLEAN_PAIRS = 40\n",
    ),
    (
        "def run_child(request_file: Path, out_path: Path) -> int:\n",
        "def child_isolation_violations(config: Any, scratch: Path) -> list[str]:\n"
        "    \"\"\"Pipeline writers/flags that would escape the pair's scratch dir (BEN-006).\n"
        "\n"
        "    Checked on the EFFECTIVE gate configuration, not on the environment the\n"
        "    harness intended: on 2026-09-13 the credential loader re-applied the operator\n"
        "    .env over the redirects and 118 benchmark decisions reached the production\n"
        "    decision cache.  A missing attribute is itself a violation, so a renamed\n"
        "    config field cannot silently disable the guard.\n"
        "    \"\"\"\n"
        "    root = Path(scratch).resolve()\n"
        "    violations: list[str] = []\n"
        "    for name in (\n"
        "        \"decision_cache_file\", \"trade_memory_file\", \"cost_report_file\",\n"
        "        \"request_response_fingerprint_file\", \"shadow_repeat_artifact_file\",\n"
        "    ):\n"
        "        if not hasattr(config, name):\n"
        "            violations.append(f\"missing_attr:{name}\")\n"
        "            continue\n"
        "        try:\n"
        "            Path(str(getattr(config, name))).resolve().relative_to(root)\n"
        "        except ValueError:\n"
        "            violations.append(name)\n"
        "    for name in (\"decision_cache_enable\", \"shadow_repeat_enable\", \"require_repeatability_live\"):\n"
        "        if not hasattr(config, name):\n"
        "            violations.append(f\"missing_attr:{name}\")\n"
        "        elif bool(getattr(config, name)):\n"
        "            violations.append(f\"{name}=True\")\n"
        "    return violations\n"
        "\n"
        "\n"
        "def run_child(request_file: Path, out_path: Path) -> int:\n",
    ),
    (
        "        if mode == \"live\":\n"
        "            import po3_env\n"
        "\n"
        "            po3_env.bootstrap_provider_env()\n"
        "        import ai_gate\n",
        "        if mode == \"live\":\n"
        "            import po3_env\n"
        "\n"
        "            po3_env.bootstrap_provider_env()\n"
        "            # bootstrap_provider_env loads python/.env with override=True, so every\n"
        "            # redirect above that the operator .env also defines had been replaced\n"
        "            # by its PRODUCTION value (measured 2026-09-13: decision cache, trade\n"
        "            # memory, cost report, fingerprints, shadow-repeat and repeatability\n"
        "            # settings).  Re-apply the isolation and the wire pin AFTER the load.\n"
        "            configure_child_env(scratch, mode=mode, bus=bus)\n"
        "            os.environ[WIRE_PROJECTION_ENV] = str(ARMS.get(arm, {}).get(\"wire_projection\") or \"canonical\")\n"
        "        import ai_gate\n"
        "\n"
        "        isolation_violations = child_isolation_violations(ai_gate.AI_CONFIG, scratch)\n"
        "        if isolation_violations:\n"
        "            # Fail the pair closed BEFORE any provider call; it is re-run on resume.\n"
        "            raise RuntimeError(\"benchmark_isolation_guard:\" + \",\".join(isolation_violations))\n",
    ),
    (
        "        return bool(result.get(\"status\") == \"ok\" and result.get(\"mode\") == mode)\n",
        "        # A pair refused by the Go usage limit never reached the model: it is\n"
        "        # not a completed observation of the arm and is re-run after the reset.\n"
        "        return bool(\n"
        "            result.get(\"status\") == \"ok\" and result.get(\"mode\") == mode and not usage_limit_refused(result)\n"
        "        )\n",
    ),
    (
        "def go_limit_hit(result: dict) -> bool:\n",
        "def usage_limit_refused(result: dict) -> bool:\n"
        "    \"\"\"True when a role's LAST attempt is a refused, unretried 429.\n"
        "\n"
        "    The attempt ledger does not carry the response body, so the literal\n"
        "    ``GoUsageLimitError`` never reaches the record: the 2026-09-13 weekly-limit\n"
        "    refusals (\"Weekly usage limit reached. Resets in ...\") arrived as\n"
        "    ``RateLimitError``, 264 times, and the run did not stop.  The transport\n"
        "    admission-retries congestion 429s, so a 429 that was refused, not billed and\n"
        "    never retried, with no later attempt for the role, is a usage-limit refusal.\n"
        "    \"\"\"\n"
        "    last_by_role: dict[str, dict] = {}\n"
        "    for attempt in result.get(\"attempts\") or []:\n"
        "        if isinstance(attempt, dict):\n"
        "            last_by_role[str(attempt.get(\"role\") or \"\")] = attempt\n"
        "    for attempt in last_by_role.values():\n"
        "        retries = attempt.get(\"retry_counts\") or {}\n"
        "        if (\n"
        "            attempt.get(\"http_status\") == 429\n"
        "            and str(attempt.get(\"billing_state\") or \"\") == \"not_billed_admission_refused\"\n"
        "            and int(retries.get(\"admission_retries\") or 0) == 0\n"
        "        ):\n"
        "            return True\n"
        "    return False\n"
        "\n"
        "\n"
        "def go_limit_hit(result: dict) -> bool:\n",
    ),
    (
        "    for attempt in result.get(\"attempts\") or []:\n"
        "        if not isinstance(attempt, dict):\n"
        "            continue\n"
        "        blob = json.dumps(attempt, default=str).lower()\n",
        "    if usage_limit_refused(result):\n"
        "        return True\n"
        "    for attempt in result.get(\"attempts\") or []:\n"
        "        if not isinstance(attempt, dict):\n"
        "            continue\n"
        "        blob = json.dumps(attempt, default=str).lower()\n",
    ),
    (
        "    def pair_stat(arm_x: str, arm_y: str) -> dict:\n"
        "        shared = [\n"
        "            rid for rid in request_ids\n"
        "            if arm_x in by_request[rid] and arm_y in by_request[rid]\n"
        "            and str(by_request[rid][arm_x].get(\"status\")) == \"ok\"\n"
        "            and str(by_request[rid][arm_y].get(\"status\")) == \"ok\"\n"
        "        ]\n",
        "    def pair_stat(arm_x: str, arm_y: str, clean_only: bool = False) -> dict:\n"
        "        shared = [\n"
        "            rid for rid in request_ids\n"
        "            if arm_x in by_request[rid] and arm_y in by_request[rid]\n"
        "            and str(by_request[rid][arm_x].get(\"status\")) == \"ok\"\n"
        "            and str(by_request[rid][arm_y].get(\"status\")) == \"ok\"\n"
        "            and not (\n"
        "                clean_only\n"
        "                and (\n"
        "                    result_pair_flags(by_request[rid][arm_x])[\"infra_failed\"]\n"
        "                    or result_pair_flags(by_request[rid][arm_y])[\"infra_failed\"]\n"
        "                )\n"
        "            )\n"
        "        ]\n",
    ),
    (
        "    summary[\"_noise_high_a_vs_high_b\"] = noise\n"
        "    for arm in arms:\n"
        "        if arm != \"muse_high_a\":\n"
        "            summary[arm][\"agreement_vs_high_a\"] = pair_stat(arm, \"muse_high_a\")\n",
        "    summary[\"_noise_high_a_vs_high_b\"] = noise\n"
        "    # Addendum A3: agreement over pairs where NEITHER side had an infrastructure\n"
        "    # outcome.  The literal BEN-004 agreement counts two degraded REJECTs as\n"
        "    # agreement; on 2026-09-13 shared usage-limit refusals inflated it that way.\n"
        "    noise_clean = pair_stat(\"muse_high_a\", \"muse_high_b\", clean_only=True) if have_both_high else None\n"
        "    summary[\"_noise_high_a_vs_high_b_clean\"] = noise_clean\n"
        "    for arm in arms:\n"
        "        if arm != \"muse_high_a\":\n"
        "            summary[arm][\"agreement_vs_high_a\"] = pair_stat(arm, \"muse_high_a\")\n"
        "            summary[arm][\"agreement_vs_high_a_clean\"] = pair_stat(arm, \"muse_high_a\", clean_only=True)\n",
    ),
    (
        "            conditions[\"non_inferior\"] = bool(conditions) and all(bool(v) for v in conditions.values())\n"
        "        summary[arm][\"ben005\"] = {\n"
        "            \"conditions\": conditions,\n"
        "            \"non_inferior\": conditions.get(\"non_inferior\", False) if conditions else None,\n"
        "        }\n",
        "            conditions[\"non_inferior\"] = bool(conditions) and all(bool(v) for v in conditions.values())\n"
        "            clean_agree = summary[arm].get(\"agreement_vs_high_a_clean\") or {}\n"
        "            clean_noise = summary.get(\"_noise_high_a_vs_high_b_clean\") or {}\n"
        "            clean_rate = (clean_agree.get(\"decision_state_agreement\") or {}).get(\"rate\")\n"
        "            clean_noise_rate = (clean_noise.get(\"decision_state_agreement\") or {}).get(\"rate\")\n"
        "            limit_refused = sum(\n"
        "                1 for rid in request_ids for other in (arm, \"muse_high_a\")\n"
        "                if other in by_request[rid] and usage_limit_refused(by_request[rid][other])\n"
        "            )\n"
        "            a3 = {\n"
        "                \"d_clean_state_agreement_ge_clean_noise_minus_5pp\": (\n"
        "                    clean_rate is not None and clean_noise_rate is not None\n"
        "                    and clean_rate >= clean_noise_rate - 0.05\n"
        "                ),\n"
        "                \"n_clean_pairs_ge_min\": int(clean_agree.get(\"n\") or 0) >= A3_MIN_CLEAN_PAIRS,\n"
        "                \"no_usage_limit_refusals_left\": limit_refused == 0,\n"
        "            }\n"
        "            literal = {\n"
        "                k: v for k, v in conditions.items()\n"
        "                if k not in (\"non_inferior\", \"d_state_agreement_ge_noise_minus_5pp\")\n"
        "            }\n"
        "            a3[\"non_inferior_a3\"] = all(bool(v) for v in literal.values()) and all(bool(v) for v in a3.values())\n"
        "            conditions[\"addendum_a3\"] = a3\n"
        "        summary[arm][\"ben005\"] = {\n"
        "            \"conditions\": conditions,\n"
        "            \"non_inferior\": conditions.get(\"non_inferior\", False) if conditions else None,\n"
        "            \"non_inferior_a3\": (conditions.get(\"addendum_a3\") or {}).get(\"non_inferior_a3\") if conditions else None,\n"
        "        }\n",
    ),
]

T = [
    ("import copy\nimport json\n", "import copy\nimport json\nimport os\n"),
    (
        "\nif __name__ == \"__main__\":\n    unittest.main()\n",
        "\nclass TakeoverHardeningTests(unittest.TestCase):\n"
        "    \"\"\"2026-09-13 takeover: usage-limit stop/resume, .env-proof isolation, A3 clean agreement.\"\"\"\n"
        "\n"
        "    @staticmethod\n"
        "    def _limit_result(request_id: str = \"REQ-L\", arm: str = \"muse_low\") -> dict:\n"
        "        return {\n"
        "            \"request_id\": request_id, \"arm\": arm, \"status\": \"ok\", \"mode\": \"live\",\n"
        "            \"decision\": {\"decision_state\": \"REJECT\", \"decision_quality_tier\": \"DEGRADED_NON_TRADING\",\n"
        "                         \"decision_source\": \"provider_transport_error\"},\n"
        "            \"attempts\": [{\n"
        "                \"role\": \"analyst\", \"outcome\": \"transport_error\", \"http_status\": 429,\n"
        "                \"error_category\": \"RateLimitError\", \"billing_state\": \"not_billed_admission_refused\",\n"
        "                \"retry_kind\": \"none\", \"retry_counts\": {\"admission_retries\": 0},\n"
        "            }],\n"
        "        }\n"
        "\n"
        "    def test_weekly_limit_refusal_stops_the_run_and_is_rerun_on_resume(self) -> None:\n"
        "        result = self._limit_result()\n"
        "        self.assertTrue(bench.go_limit_hit(result))\n"
        "        with tempfile.TemporaryDirectory() as raw:\n"
        "            results_dir = Path(raw)\n"
        "            bench.write_json(bench.result_path(results_dir, \"REQ-L\", \"muse_low\"), result)\n"
        "            self.assertFalse(bench.completed_ok(results_dir, \"REQ-L\", \"muse_low\", mode=\"live\"))\n"
        "\n"
        "    def test_congestion_429_recovered_by_admission_retry_is_not_a_limit(self) -> None:\n"
        "        result = self._limit_result()\n"
        "        result[\"attempts\"].append({\n"
        "            \"role\": \"analyst\", \"outcome\": \"ok\", \"http_status\": 200, \"billing_state\": \"billed_usage_reported\",\n"
        "            \"retry_kind\": \"admission\", \"retry_counts\": {\"admission_retries\": 1},\n"
        "        })\n"
        "        result[\"decision\"] = {\"decision_state\": \"ABSTAIN\", \"decision_quality_tier\": \"FULL_STRUCTURED\"}\n"
        "        self.assertFalse(bench.usage_limit_refused(result))\n"
        "        self.assertFalse(bench.go_limit_hit(result))\n"
        "\n"
        "    def test_isolation_survives_an_env_file_that_overrides_the_redirects(self) -> None:\n"
        "        with tempfile.TemporaryDirectory() as raw, patch.dict(os.environ, {}, clear=False):\n"
        "            scratch = Path(raw) / \"pair\"\n"
        "            bench.configure_child_env(scratch, mode=\"dry\", bus=Path(raw))\n"
        "            clean = ai_gate.AIGateRuntimeConfig.from_env(dict(os.environ))\n"
        "            self.assertEqual(bench.child_isolation_violations(clean, scratch), [])\n"
        "            # What po3_env.bootstrap_provider_env(override=True) did on 2026-09-13.\n"
        "            os.environ[\"AI_DECISION_CACHE_FILE\"] = str(bench.REPO_PYTHON / \"data\" / \"ai_decision_cache.jsonl\")\n"
        "            os.environ[\"AI_SHADOW_REPEAT_ENABLE\"] = \"true\"\n"
        "            leaked = ai_gate.AIGateRuntimeConfig.from_env(dict(os.environ))\n"
        "            violations = bench.child_isolation_violations(leaked, scratch)\n"
        "            self.assertIn(\"decision_cache_file\", violations)\n"
        "            self.assertIn(\"shadow_repeat_enable=True\", violations)\n"
        "            bench.configure_child_env(scratch, mode=\"dry\", bus=Path(raw))\n"
        "            restored = ai_gate.AIGateRuntimeConfig.from_env(dict(os.environ))\n"
        "            self.assertEqual(bench.child_isolation_violations(restored, scratch), [])\n"
        "\n"
        "    def test_live_child_reapplies_isolation_after_the_credential_load(self) -> None:\n"
        "        source = Path(bench.__file__).read_text(encoding=\"utf-8\")\n"
        "        body = source[source.index(\"def run_child(\"):]\n"
        "        bootstrap = body.index(\"po3_env.bootstrap_provider_env()\")\n"
        "        self.assertGreater(body.index(\"configure_child_env(scratch, mode=mode, bus=bus)\", bootstrap), bootstrap)\n"
        "        self.assertLess(bootstrap, body.index(\"child_isolation_violations(ai_gate.AI_CONFIG, scratch)\"))\n"
        "\n"
        "    def test_clean_agreement_ignores_shared_infrastructure_rejects(self) -> None:\n"
        "        high = _mk_decision(state=\"ABSTAIN\", allow=False, candidate_id=\"A\", score=6.0)\n"
        "        manifest = {\"rows\": [{\"request_id\": f\"R{i}\"} for i in range(4)]}\n"
        "        results = []\n"
        "        for i in range(4):\n"
        "            for arm in (\"muse_high_a\", \"muse_high_b\"):\n"
        "                results.append(_mk_result(f\"R{i}\", arm, copy.deepcopy(high)))\n"
        "            if i < 2:\n"
        "                arm_result = _mk_result(f\"R{i}\", \"muse_low\", _mk_decision(state=\"REJECT\", allow=False, candidate_id=\"A\", score=4.0), effort=\"low\")\n"
        "            else:\n"
        "                arm_result = _mk_result(f\"R{i}\", \"muse_low\", copy.deepcopy(high), effort=\"low\")\n"
        "            results.append(arm_result)\n"
        "        # Two requests where BOTH sides were refused by the usage limit.\n"
        "        for i in range(4, 6):\n"
        "            manifest[\"rows\"].append({\"request_id\": f\"R{i}\"})\n"
        "            for arm in (\"muse_high_a\", \"muse_high_b\", \"muse_low\"):\n"
        "                results.append(self._limit_result(f\"R{i}\", arm))\n"
        "        summary = bench.aggregate_results(manifest, results)\n"
        "        literal = summary[\"muse_low\"][\"agreement_vs_high_a\"][\"decision_state_agreement\"]\n"
        "        clean = summary[\"muse_low\"][\"agreement_vs_high_a_clean\"][\"decision_state_agreement\"]\n"
        "        self.assertEqual((literal[\"numerator\"], literal[\"denominator\"]), (4, 6))\n"
        "        self.assertEqual((clean[\"numerator\"], clean[\"denominator\"]), (2, 4))\n"
        "        a3 = summary[\"muse_low\"][\"ben005\"][\"conditions\"][\"addendum_a3\"]\n"
        "        self.assertFalse(a3[\"no_usage_limit_refusals_left\"])\n"
        "        self.assertFalse(a3[\"n_clean_pairs_ge_min\"])\n"
        "        self.assertFalse(summary[\"muse_low\"][\"ben005\"][\"non_inferior_a3\"])\n"
        "\n"
        "\nif __name__ == \"__main__\":\n    unittest.main()\n",
    ),
]


def patch(path: Path, edits) -> str:
    text = path.read_text(encoding="utf-8")
    for old, new in edits:
        count = text.count(old)
        if count != 1:
            raise SystemExit(f"{path.name}: expected exactly 1 match, found {count}: {old[:70]!r}")
        text = text.replace(old, new)
    return text


def atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    py_compile.compile(tmp, doraise=True)
    os.replace(tmp, path)


def main() -> int:
    targets = [
        (REPO_PY / "tools" / "opencode_reasoning_benchmark.py", H),
        (REPO_PY / "tests" / "test_opencode_reasoning_benchmark.py", T),
    ]
    patched = [(path, patch(path, edits)) for path, edits in targets]
    for path, text in patched:
        atomic_write(path, text)
        print(f"patched {path.relative_to(REPO_PY)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
