"""
tests/test_dev_team.py — опциональный шаблон команды разработки (KON-129, пункт 2).

Covers:
  - в шаблоне есть все пять агентов с валидным frontmatter и маркером {{TOOLS}};
  - init_dev_team.py подставляет все маркеры, не оставляет привязок к исходному проекту,
    добавляет MCP трекера в tools, фильтрует роли, не перезаписывает без --force и не пишет при --dry-run;
  - неверный project_id отклоняется.
Скрипт запускается подпроцессом во временной копии (скрипт + templates/dev_team): проверяется то, что получит пользователь.
"""

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE = PROJECT_ROOT / "templates" / "dev_team"
ROLES = ("arch", "ux", "dev", "qa", "devops")
# слова исходного проекта, которых в обезличенном шаблоне быть не должно
FORBIDDEN = ("re_web_presale", "Jira", "LDAP", "OpenCode", "Константин", "KON-", "Pre.", "Linear",
             "FastAPI", "React", "ScriptRunner")


class DevTeamInstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copy2(PROJECT_ROOT / "init_dev_team.py", self.tmp / "init_dev_team.py")
        shutil.copytree(SOURCE, self.tmp / "templates" / "dev_team")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_init(self, *args):
        return subprocess.run(
            [sys.executable, "-I", str(self.tmp / "init_dev_team.py"), *args],
            capture_output=True, text=True, encoding="utf-8", cwd=self.tmp,
        )

    def agents_dir(self):
        return self.tmp / ".claude" / "agents"

    # -- шаблон --
    def test_template_has_all_agents_with_frontmatter(self):
        for role in ROLES:
            text = (SOURCE / "agents" / f"{role}.md").read_text(encoding="utf-8")
            m = re.match(r"^---\nname: (\w+)\ndescription: (.+)\ntools: (.+)\n---\n", text)
            self.assertIsNotNone(m, role)
            self.assertEqual(m.group(1), role)
            self.assertEqual(m.group(3).strip(), "{{TOOLS}}")

    # -- установка --
    def test_install_renders_everything(self):
        r = self.run_init("--project-id", "demo_proj", "--title", "Демо", "--owner", "Иван",
                          "--tracker", "Трекер", "--task-prefix", "D.")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        files = [self.agents_dir() / f"{role}.md" for role in ROLES] + [
            self.tmp / ".claude" / "rules" / "dev_team.md",
            self.tmp / ".claude" / "rules" / "ui_workflow.md",
            self.tmp / ".claude" / "skills" / "ux_design" / "SKILL.md",
        ]
        for f in files:
            self.assertTrue(f.exists(), f)
            text = f.read_text(encoding="utf-8")
            self.assertNotIn("{{", text, f"{f.name}: остался маркер")
        dev = (self.agents_dir() / "dev.md").read_text(encoding="utf-8")
        self.assertIn("проекта `demo_proj`", dev)
        self.assertIn("D.Dev-N", dev)
        self.assertIn("Иван", dev)
        self.assertIn("tools: Bash, Read, Write, Edit, Glob, Grep, ToolSearch\n", dev)

    def test_no_traces_of_source_project(self):
        self.run_init("--project-id", "demo_proj")
        for f in (self.tmp / ".claude").rglob("*.md"):
            text = f.read_text(encoding="utf-8")
            for word in FORBIDDEN:
                self.assertNotIn(word, text, f"{f.name}: найдено «{word}»")

    def test_tracker_mcp_goes_to_tools(self):
        self.run_init("--project-id", "demo_proj", "--tracker-mcp", "mcp__tracker")
        for role in ROLES:
            text = (self.agents_dir() / f"{role}.md").read_text(encoding="utf-8")
            self.assertIn("ToolSearch, mcp__tracker\n", text, role)

    def test_roles_filter(self):
        r = self.run_init("--project-id", "demo_proj", "--roles", "arch,dev")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        names = sorted(p.name for p in self.agents_dir().glob("*.md"))
        self.assertEqual(names, ["arch.md", "dev.md"])
        self.assertTrue((self.tmp / ".claude" / "rules" / "dev_team.md").exists())
        self.assertTrue((self.tmp / ".claude" / "rules" / "ui_workflow.md").exists())
        self.assertFalse((self.tmp / ".claude" / "skills" / "ux_design").exists())

    def test_unknown_role_rejected(self):
        r = self.run_init("--project-id", "demo_proj", "--roles", "arch,boss")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(self.agents_dir().exists())

    def test_existing_files_kept_without_force(self):
        self.agents_dir().mkdir(parents=True)
        (self.agents_dir() / "dev.md").write_text("мой агент", encoding="utf-8")
        r = self.run_init("--project-id", "demo_proj")
        self.assertEqual(r.returncode, 0)
        self.assertEqual((self.agents_dir() / "dev.md").read_text(encoding="utf-8"), "мой агент")
        self.assertIn("пропущен", r.stdout)
        r2 = self.run_init("--project-id", "demo_proj", "--force")
        self.assertEqual(r2.returncode, 0)
        self.assertNotEqual((self.agents_dir() / "dev.md").read_text(encoding="utf-8"), "мой агент")

    def test_dry_run_writes_nothing(self):
        r = self.run_init("--project-id", "demo_proj", "--dry-run")
        self.assertEqual(r.returncode, 0)
        self.assertIn("будет создан", r.stdout)
        self.assertFalse((self.tmp / ".claude").exists())

    def test_bad_project_id_rejected(self):
        for bad in ("Demo Proj", "demo__proj", "_demo", "демо"):
            r = self.run_init("--project-id", bad)
            self.assertNotEqual(r.returncode, 0, bad)
        self.assertFalse((self.tmp / ".claude").exists())

    def test_project_id_detected_from_output_data(self):
        (self.tmp / "output" / "data" / "auto_proj").mkdir(parents=True)
        r = self.run_init()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("проекта `auto_proj`",
                      (self.agents_dir() / "dev.md").read_text(encoding="utf-8"))

    def test_missing_project_id_is_an_error(self):
        r = self.run_init()
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
