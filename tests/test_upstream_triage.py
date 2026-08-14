import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "upstream_triage.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "upstream-watch.yml"
UPSTREAM_SLUG = "MadsLorentzen/ai-job-search"


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


class TriageRepoFixture(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "tools").mkdir()
        shutil.copy(SCRIPT, self.root / "tools" / "upstream_triage.py")
        (self.root / ".github").mkdir()
        git(self.root, "init", "-b", "master")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.com")
        git(self.root, "remote", "add", "upstream", f"https://github.com/{UPSTREAM_SLUG}.git")
        self.write("shared.txt", "base\n")
        self.write("kept.py", "print('hi')\n")
        self.commit("init")

    def write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def commit(self, msg: str) -> str:
        git(self.root, "add", "-A")
        git(self.root, "commit", "-m", msg)
        return git(self.root, "rev-parse", "HEAD").strip()

    def set_upstream_to_head(self) -> None:
        git(self.root, "update-ref", "refs/remotes/upstream/master", "HEAD")

    def run_triage(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(self.root / "tools" / "upstream_triage.py"), *args], cwd=self.root, capture_output=True, text=True)


class UpToDateTests(TriageRepoFixture):
    def test_reports_up_to_date_when_not_behind(self):
        self.set_upstream_to_head()
        result = self.run_triage()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Up to date", result.stdout)


class RelevanceFilterTests(TriageRepoFixture):
    def test_commit_touching_only_removed_files_is_skipped(self):
        self.write("portals/removed_portal.py", "x = 1\n")
        self.commit("upstream: add removed_portal")
        self.set_upstream_to_head()
        git(self.root, "reset", "--hard", "HEAD~1")
        result = self.run_triage()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("touches only files not in this fork", result.stdout)

    def test_commit_touching_kept_files_is_worth_reviewing(self):
        self.write("kept.py", "print('changed')\n")
        self.commit("upstream: change kept.py")
        self.set_upstream_to_head()
        git(self.root, "reset", "--hard", "HEAD~1")
        result = self.run_triage()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Worth reviewing", result.stdout)
        self.assertIn("git cherry-pick", result.stdout)

    def test_changelog_only_footprint_is_skipped(self):
        self.write("portals/gone.py", "y = 2\n")
        self.write("CHANGELOG.md", "- did a thing\n")
        self.commit("upstream: feature living in removed area + changelog")
        self.set_upstream_to_head()
        git(self.root, "reset", "--hard", "HEAD~1")
        self.write("CHANGELOG.md", "- fork changelog\n")
        self.commit("fork changelog")
        result = self.run_triage()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("changelog-only footprint in this fork", result.stdout)


class AlreadyAppliedTests(TriageRepoFixture):
    def test_cherry_picked_commit_drops_off_via_patch_id(self):
        self.write("kept.py", "print('feature')\n")
        upstream_sha = self.commit("upstream: add feature")
        self.write("shared.txt", "upstream edit\n")
        self.commit("upstream: unrelated change")
        self.set_upstream_to_head()
        git(self.root, "reset", "--hard", "HEAD~2")
        self.write("fork_only.txt", "mine\n")
        self.commit("fork: divergent commit")
        git(self.root, "cherry-pick", upstream_sha)
        result = self.run_triage()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already applied (cherry-picked)", result.stdout)


class WontPortTests(TriageRepoFixture):
    def test_listed_sha_is_excluded(self):
        self.write("kept.py", "print('rejected feature')\n")
        rejected = self.commit("upstream: feature the fork rejects")
        self.set_upstream_to_head()
        git(self.root, "reset", "--hard", "HEAD~1")
        self.write(".github/upstream-wontport.txt", f"{rejected[:9]}  # rejected on purpose\n")
        self.commit("fork: won't-port list")
        result = self.run_triage()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("on the fork's won't-port list", result.stdout)


class MissingUpstreamRefTests(TriageRepoFixture):
    def test_missing_ref_degrades_gracefully(self):
        result = self.run_triage()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("was not available", result.stdout)


class WorkflowGuardTests(unittest.TestCase):
    def test_workflow_is_guarded_against_upstream(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(f"github.repository != '{UPSTREAM_SLUG}'", text)

    def test_workflow_is_read_only_and_does_not_require_issues(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("contents: read", text)
        self.assertNotIn("issues: write", text)
        self.assertNotIn("gh issue", text)
        self.assertNotIn("secrets.", text)
        self.assertIn("GITHUB_STEP_SUMMARY", text)

    def test_actions_are_sha_pinned(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("- uses:") or stripped.startswith("uses:"):
                ref = stripped.split("uses:", 1)[1].strip()
                self.assertIn("@", ref)
                sha = ref.split("@", 1)[1].split()[0]
                self.assertRegex(sha, r"^[0-9a-f]{40}$", f"action not SHA-pinned: {ref}")


if __name__ == "__main__":
    unittest.main()
