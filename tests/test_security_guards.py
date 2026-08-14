import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GUARD_SCRIPT = REPO_ROOT / "tools" / "security_guards.py"

sys.path.insert(0, str(REPO_ROOT / "tools"))
import security_guards  # noqa: E402  (imported for its allowlist constants)


def run_guards(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(root / "tools" / "security_guards.py")],
        capture_output=True,
        text=True,
    )


class GuardRepoFixture(unittest.TestCase):
    """Builds a minimal repo tree the guards pass on, then breaks one thing per test."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

        (self.root / "tools").mkdir()
        shutil.copy(GUARD_SCRIPT, self.root / "tools" / "security_guards.py")

        self.settings = self.root / ".claude" / "settings.json"
        self.settings.parent.mkdir()
        self.write_settings(sorted(security_guards.ALLOWED_PERMISSIONS))

        self.gitignore = self.root / ".gitignore"
        self.write_gitignore(security_guards.REQUIRED_IGNORE_RULES)

        self.manifest = self.root / ".agents" / "skills" / "example-search" / "cli" / "package.json"
        self.manifest.parent.mkdir(parents=True)
        self.write_manifest({"name": "example-cli", "scripts": {"start": "bun run src/cli.ts"}})

    def write_settings(self, allow):
        self.settings.write_text(json.dumps({"permissions": {"allow": list(allow)}}))

    def write_gitignore(self, rules):
        self.gitignore.write_text("\n".join(rules) + "\n")

    def write_manifest(self, data, path=None):
        (path or self.manifest).write_text(json.dumps(data))


class CleanTreeTests(GuardRepoFixture):
    def test_clean_tree_passes(self):
        result = run_guards(self.root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("security_guards: OK", result.stdout)


class PermissionGuardTests(GuardRepoFixture):
    def test_wildcard_bash_permission_fails(self):
        self.write_settings(sorted(security_guards.ALLOWED_PERMISSIONS) + ["Bash(*)"])
        result = run_guards(self.root)
        self.assertEqual(result.returncode, 1)
        self.assertIn("not in the reviewed allowlist", result.stdout)
        self.assertIn("Bash(*)", result.stdout)

    def test_network_fetch_permission_fails(self):
        self.write_settings(sorted(security_guards.ALLOWED_PERMISSIONS) + ["Bash(curl:*)"])
        result = run_guards(self.root)
        self.assertEqual(result.returncode, 1)
        self.assertIn("not in the reviewed allowlist", result.stdout)

    def test_dropped_allowlisted_permission_still_passes(self):
        allow = sorted(security_guards.ALLOWED_PERMISSIONS)[:-1]
        self.write_settings(allow)
        result = run_guards(self.root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_invalid_settings_json_fails(self):
        self.settings.write_text("{not json")
        result = run_guards(self.root)
        self.assertEqual(result.returncode, 1)
        self.assertIn("invalid JSON", result.stdout)

    def test_malformed_settings_shape_fails_cleanly(self):
        for data, message in [
            ([], "top-level JSON value must be an object"),
            ({"permissions": []}, "permissions must be an object"),
            ({"permissions": {"allow": "Bash(*)"}}, "permissions.allow must be a list of strings"),
            ({"permissions": {"allow": [1]}}, "permissions.allow must be a list of strings"),
        ]:
            with self.subTest(data=data):
                self.settings.write_text(json.dumps(data))
                result = run_guards(self.root)
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stdout)
                self.assertNotIn("Traceback", result.stderr)


class HookGuardTests(GuardRepoFixture):
    def write_settings_with_hooks(self, hooks):
        self.settings.write_text(
            json.dumps(
                {
                    "permissions": {"allow": sorted(security_guards.ALLOWED_PERMISSIONS)},
                    "hooks": hooks,
                }
            )
        )

    def test_session_start_hook_fails(self):
        self.write_settings_with_hooks(
            {
                "SessionStart": [
                    {"hooks": [{"type": "command", "command": "node .claude/math_init.js"}]}
                ]
            }
        )
        result = run_guards(self.root)
        self.assertEqual(result.returncode, 1)
        self.assertIn("hook not in the reviewed allowlist", result.stdout)
        self.assertIn("math_init.js", result.stdout)

    def test_hook_is_caught_even_when_permissions_block_is_malformed(self):
        self.settings.write_text(
            json.dumps(
                {
                    "permissions": {"allow": "not-a-list"},
                    "hooks": {
                        "SessionStart": [
                            {"hooks": [{"type": "command", "command": "echo unreviewed"}]}
                        ]
                    },
                }
            )
        )
        result = run_guards(self.root)
        self.assertEqual(result.returncode, 1)
        self.assertIn("hook not in the reviewed allowlist", result.stdout)
        self.assertIn("permissions.allow must be a list of strings", result.stdout)

    def test_every_hook_event_is_checked(self):
        for event in ["SessionStart", "PreToolUse", "PostToolUse", "Stop", "UserPromptSubmit"]:
            with self.subTest(event=event):
                self.write_settings_with_hooks(
                    {event: [{"hooks": [{"type": "command", "command": "echo test"}]}]}
                )
                result = run_guards(self.root)
                self.assertEqual(result.returncode, 1)
                self.assertIn("hook not in the reviewed allowlist", result.stdout)

    def test_every_command_in_multi_hook_event_is_reported(self):
        self.write_settings_with_hooks(
            {
                "SessionStart": [
                    {"hooks": [{"type": "command", "command": "first.sh"}]},
                    {"hooks": [{"type": "command", "command": "second.sh"}]},
                ]
            }
        )
        result = run_guards(self.root)
        self.assertEqual(result.returncode, 1)
        self.assertIn("first.sh", result.stdout)
        self.assertIn("second.sh", result.stdout)

    def test_unrecognised_hook_shapes_fail_closed(self):
        for hooks in [
            {"SessionStart": "echo test"},
            {"SessionStart": ["echo test"]},
            {"SessionStart": [{"hooks": "echo test"}]},
            {"SessionStart": [{"hooks": [{"type": "command"}]}]},
            {"SessionStart": [{"hooks": [{"type": "command", "command": 42}]}]},
        ]:
            with self.subTest(hooks=hooks):
                self.write_settings_with_hooks(hooks)
                result = run_guards(self.root)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertNotIn("Traceback", result.stderr)

    def test_non_object_hooks_value_fails_cleanly(self):
        self.write_settings_with_hooks(["SessionStart"])
        result = run_guards(self.root)
        self.assertEqual(result.returncode, 1)
        self.assertIn("hooks must be an object", result.stdout)
        self.assertNotIn("Traceback", result.stderr)

    def test_absent_or_empty_hooks_pass(self):
        for hooks in [{}, {"SessionStart": []}]:
            with self.subTest(hooks=hooks):
                self.write_settings_with_hooks(hooks)
                result = run_guards(self.root)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_allowlisted_hook_passes(self):
        command = "SessionStart:echo reviewed"
        guard = self.root / "tools" / "security_guards.py"
        guard.write_text(
            guard.read_text(encoding="utf-8").replace(
                "ALLOWED_HOOKS: set[str] = set()",
                f"ALLOWED_HOOKS: set[str] = {{{command!r}}}",
            ),
            encoding="utf-8",
        )
        self.write_settings_with_hooks(
            {"SessionStart": [{"hooks": [{"type": "command", "command": "echo reviewed"}]}]}
        )
        result = run_guards(self.root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class GitignoreGuardTests(GuardRepoFixture):
    def test_each_missing_personal_data_rule_fails(self):
        for rule in security_guards.REQUIRED_IGNORE_RULES:
            with self.subTest(rule=rule):
                remaining = [r for r in security_guards.REQUIRED_IGNORE_RULES if r != rule]
                self.write_gitignore(remaining)
                result = run_guards(self.root)
                self.assertEqual(result.returncode, 1)
                self.assertIn("required personal-data rule missing", result.stdout)
                self.assertIn(rule, result.stdout)
        self.write_gitignore(security_guards.REQUIRED_IGNORE_RULES)

    def test_extra_rules_are_allowed(self):
        self.write_gitignore(list(security_guards.REQUIRED_IGNORE_RULES) + ["*.bak", "scratch/"])
        result = run_guards(self.root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class GitignoreNegationTests(GuardRepoFixture):
    def test_negation_reincluding_personal_data_fails(self):
        self.write_gitignore(list(security_guards.REQUIRED_IGNORE_RULES) + ["!salary_data.json"])
        result = run_guards(self.root)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("negation rule not in the reviewed allowlist", result.stdout)
        self.assertIn("!salary_data.json", result.stdout)

    def test_allowlisted_negations_pass(self):
        self.write_gitignore(
            list(security_guards.REQUIRED_IGNORE_RULES)
            + sorted(security_guards.ALLOWED_IGNORE_NEGATIONS)
        )
        result = run_guards(self.root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class ManifestGuardTests(GuardRepoFixture):
    def test_each_lifecycle_script_fails(self):
        for script in sorted(security_guards.FORBIDDEN_SCRIPTS):
            with self.subTest(script=script):
                self.write_manifest({"name": "example-cli", "scripts": {script: "echo test"}})
                result = run_guards(self.root)
                self.assertEqual(result.returncode, 1)
                self.assertIn("lifecycle script", result.stdout)
                self.assertIn(script, result.stdout)
        self.write_manifest({"name": "example-cli", "scripts": {}})

    def test_trusted_dependencies_fails(self):
        self.write_manifest({"name": "example-cli", "trustedDependencies": ["left-pad"]})
        result = run_guards(self.root)
        self.assertEqual(result.returncode, 1)
        self.assertIn("trustedDependencies", result.stdout)

    def test_malformed_manifest_shape_fails_cleanly(self):
        for data, message in [
            ([], "top-level JSON value must be an object"),
            ({"name": "example-cli", "scripts": []}, "scripts must be an object"),
        ]:
            with self.subTest(data=data):
                self.write_manifest(data)
                result = run_guards(self.root)
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stdout)
                self.assertNotIn("Traceback", result.stderr)

    def test_benign_scripts_pass(self):
        self.write_manifest(
            {"name": "example-cli", "scripts": {"start": "bun run src/cli.ts", "test": "bun test", "typecheck": "tsc --noEmit"}}
        )
        result = run_guards(self.root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_node_modules_manifests_are_ignored(self):
        nm = self.manifest.parent / "node_modules" / "some-dep" / "package.json"
        nm.parent.mkdir(parents=True)
        self.write_manifest({"name": "some-dep", "scripts": {"postinstall": "echo test"}}, path=nm)
        result = run_guards(self.root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_no_manifests_at_all_fails(self):
        self.manifest.unlink()
        result = run_guards(self.root)
        self.assertEqual(result.returncode, 1)
        self.assertIn("no package.json files found", result.stdout)


class RealRepoTests(unittest.TestCase):
    def test_guards_pass_on_this_repo(self):
        result = run_guards(REPO_ROOT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
