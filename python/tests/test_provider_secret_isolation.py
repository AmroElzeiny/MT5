"""One provider-selection authority shared by every module that bootstraps env.

The measured defect these tests pin down: ``expectancy_report`` resolved the
provider selection from the legacy ``AI_USE_REMOTE_API`` boolean alone.  That key
is absent whenever the modern ``AI_PROVIDER_SELECT`` is the one configured, and an
absent boolean read as "not remote", so under ``AI_PROVIDER_SELECT=openai_remote``
the module took its local branch: it re-loaded the whole env file -- re-injecting
the OpenRouter and local secrets that ``ai_gate``'s bootstrap had just stripped --
and then popped ``OPENAI_API_KEY``, the credential the selected provider needs.

``ai_gate`` imports ``expectancy_report`` after its own bootstrap, so the
disagreeing module ran last and won.  A correct ``openai_remote`` configuration
therefore failed closed with ``OPENAI_API_KEY=missing_remote`` while the
non-selected providers' secrets sat in the process -- both halves of the secret
isolation contract inverted at once.

These run in subprocesses because the behaviour under test is module-level import
side effects, which happen exactly once per interpreter.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import po3_env  # noqa: E402
from po3_env import (  # noqa: E402
    PROVIDER_SECRET_KEYS,
    PROVIDER_SELECT_LOCAL,
    PROVIDER_SELECT_OPENAI,
    PROVIDER_SELECT_OPENROUTER,
    PROVIDER_SELECT_VALUES,
    excluded_secret_keys,
)

_ENV_TEMPLATE = """\
AI_PROVIDER_SELECT={select}
OPENAI_API_KEY=sk-test-openai
OPENROUTER_API_KEY=sk-or-test-openrouter
OPENROUTER_MODEL=z-ai/glm-5.3-flash
LOCAL_AI_API_KEY=local-test-key
LOCAL_AI_BASE_URL=http://127.0.0.1:1234/v1
LOCAL_AI_MODEL=test-local-model
AI_GATE_MODEL=gpt-5.6-luna
AI_SERVICE_TIER=flex
"""


def _run_in_subprocess(env_file: Path, body: str) -> dict:
    """Import under a named env file and return the JSON the body prints."""

    prelude = "\n".join(
        (
            "import json, os, sys",
            f"sys.path.insert(0, {str(ROOT)!r})",
            f"os.environ['PO3_DOTENV_FILE'] = {str(env_file)!r}",
            "for _k in ('OPENAI_API_KEY', 'OPENROUTER_API_KEY', 'LOCAL_AI_API_KEY'):",
            "    os.environ.pop(_k, None)",
        )
    )
    script = prelude + "\n" + textwrap.dedent(body) + "\n"
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=600,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"subprocess failed rc={proc.returncode}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    import json

    return json.loads(proc.stdout.strip().splitlines()[-1])


class ProviderSecretIsolationTests(unittest.TestCase):
    def _write_env(self, selection: str) -> Path:
        path = Path(self.tmp.name) / f".env.{selection}"
        path.write_text(_ENV_TEMPLATE.format(select=selection), encoding="utf-8")
        return path

    def setUp(self) -> None:
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_every_selection_keeps_only_its_own_secret_after_full_import(self) -> None:
        """The end-to-end invariant, through the real ``ai_gate`` import chain.

        This is the test that fails on the pre-fix tree: for ``openai_remote`` it
        reported ``OPENAI_API_KEY`` absent and ``OPENROUTER_API_KEY`` present.
        """

        for selection in PROVIDER_SELECT_VALUES:
            with self.subTest(selection=selection):
                env_file = self._write_env(selection)
                result = _run_in_subprocess(
                    env_file,
                    "import ai_gate\n"
                    "print(json.dumps({\n"
                    "    'select': ai_gate.AI_CONFIG.provider_select,\n"
                    "    'valid': ai_gate.AI_CONFIG.provider_config_valid,\n"
                    "    'errors': list(ai_gate.AI_CONFIG.provider_config_errors),\n"
                    "    'present': {k: bool(os.environ.get(k)) for k in (\n"
                    "        'OPENAI_API_KEY', 'OPENROUTER_API_KEY', 'LOCAL_AI_API_KEY')},\n"
                    "}))",
                )
                self.assertEqual(result["select"], selection)
                owned = PROVIDER_SECRET_KEYS[selection]
                for key, present in result["present"].items():
                    if key in owned:
                        self.assertTrue(
                            present,
                            f"{selection}: {key} is the selected provider's credential "
                            "and was stripped from the process",
                        )
                    else:
                        self.assertFalse(
                            present,
                            f"{selection}: {key} belongs to a non-selected provider "
                            "and must never reach the process",
                        )

    def test_selected_provider_config_is_valid_after_full_import(self) -> None:
        """A correct configuration must not fail closed on a missing credential."""

        env_file = self._write_env(PROVIDER_SELECT_OPENAI)
        result = _run_in_subprocess(
            env_file,
            "import ai_gate\n"
            "print(json.dumps({\n"
            "    'valid': ai_gate.AI_CONFIG.provider_config_valid,\n"
            "    'errors': list(ai_gate.AI_CONFIG.provider_config_errors),\n"
            "    'model': ai_gate.AI_CONFIG.model,\n"
            "    'service_tier': ai_gate.AI_CONFIG.service_tier,\n"
            "}))",
        )
        self.assertEqual(result["errors"], [])
        self.assertTrue(result["valid"])
        self.assertEqual(result["model"], "gpt-5.6-luna")
        self.assertEqual(result["service_tier"], "flex")

    def test_importing_expectancy_report_does_not_change_the_selection(self) -> None:
        """The precise mechanism: the late importer must not win.

        ``ai_gate`` imports ``expectancy_report`` after bootstrapping, so if that
        module re-derives the selection it silently overrides a correct one.
        """

        env_file = self._write_env(PROVIDER_SELECT_OPENAI)
        result = _run_in_subprocess(
            env_file,
            "import po3_env\n"
            "po3_env.bootstrap_provider_env()\n"
            "before = {k: bool(os.environ.get(k)) for k in (\n"
            "    'OPENAI_API_KEY', 'OPENROUTER_API_KEY', 'LOCAL_AI_API_KEY')}\n"
            "import expectancy_report\n"
            "after = {k: bool(os.environ.get(k)) for k in (\n"
            "    'OPENAI_API_KEY', 'OPENROUTER_API_KEY', 'LOCAL_AI_API_KEY')}\n"
            "print(json.dumps({'before': before, 'after': after,\n"
            "    'remote_mode': expectancy_report._EXPECTANCY_REMOTE_MODE}))",
        )
        self.assertEqual(
            result["before"],
            result["after"],
            "importing expectancy_report changed which provider secrets exist",
        )
        self.assertTrue(result["before"]["OPENAI_API_KEY"])
        self.assertTrue(
            result["remote_mode"],
            "expectancy_report read AI_PROVIDER_SELECT=openai_remote as not-remote; "
            "it is resolving the selection from the legacy boolean again",
        )

    def test_bootstrap_is_idempotent(self) -> None:
        """Running the bootstrap twice must not re-inject what it stripped."""

        env_file = self._write_env(PROVIDER_SELECT_OPENROUTER)
        result = _run_in_subprocess(
            env_file,
            "import po3_env\n"
            "po3_env.bootstrap_provider_env()\n"
            "once = {k: bool(os.environ.get(k)) for k in (\n"
            "    'OPENAI_API_KEY', 'OPENROUTER_API_KEY', 'LOCAL_AI_API_KEY')}\n"
            "po3_env.bootstrap_provider_env()\n"
            "twice = {k: bool(os.environ.get(k)) for k in (\n"
            "    'OPENAI_API_KEY', 'OPENROUTER_API_KEY', 'LOCAL_AI_API_KEY')}\n"
            "print(json.dumps({'once': once, 'twice': twice}))",
        )
        self.assertEqual(result["once"], result["twice"])
        self.assertTrue(result["once"]["OPENROUTER_API_KEY"])
        self.assertFalse(result["once"]["OPENAI_API_KEY"])

    def test_unusable_selector_keeps_every_secret_out(self) -> None:
        self.assertEqual(
            set(excluded_secret_keys(None)),
            {key for keys in PROVIDER_SECRET_KEYS.values() for key in keys},
        )

    def test_excluded_keys_never_include_the_selected_providers_own(self) -> None:
        for selection in PROVIDER_SELECT_VALUES:
            with self.subTest(selection=selection):
                excluded = set(excluded_secret_keys(selection))
                for key in PROVIDER_SECRET_KEYS[selection]:
                    self.assertNotIn(key, excluded)

    def test_ai_gate_re_exports_the_shared_authority_not_a_copy(self) -> None:
        """A second definition is how the two drifted in the first place."""

        import ai_gate

        self.assertIs(ai_gate._PROVIDER_SECRET_KEYS, po3_env.PROVIDER_SECRET_KEYS)
        self.assertIs(ai_gate.resolve_provider_select, po3_env.resolve_provider_select)
        self.assertEqual(ai_gate.PROVIDER_SELECT_OPENAI, PROVIDER_SELECT_OPENAI)
        self.assertEqual(ai_gate.PROVIDER_SELECT_LOCAL, PROVIDER_SELECT_LOCAL)
        self.assertEqual(ai_gate.PROVIDER_SELECT_OPENROUTER, PROVIDER_SELECT_OPENROUTER)


if __name__ == "__main__":
    unittest.main()
