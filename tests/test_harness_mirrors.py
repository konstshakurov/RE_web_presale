"""
tests/test_harness_mirrors.py — канон агентов и правил `.agents/` и его зеркала.

Covers:
  - зеркала `.claude/` и `.codex/` совпадают с каноном (`scripts/sync_harness_mirrors.py --check`);
  - в каноне и зеркалах нет маркеров шаблонизатора `{{...}}`;
  - у каждого файла правил канона есть строка «Источник для: …»;
  - TOML-зеркала Codex разбираются и содержат name, description, developer_instructions.
"""

import re
import subprocess
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = PROJECT_ROOT / "scripts" / "sync_harness_mirrors.py"
CANON_RULES = PROJECT_ROOT / ".agents" / "rules"
CANON_AGENTS = PROJECT_ROOT / ".agents" / "agents"
MIRROR_DIRS = (PROJECT_ROOT / ".claude" / "rules", PROJECT_ROOT / ".claude" / "agents",
               PROJECT_ROOT / ".codex" / "rules", PROJECT_ROOT / ".codex" / "agents")
PLACEHOLDER_RE = re.compile(r"\{\{[A-Z_]+\}\}")


class HarnessMirrorTests(unittest.TestCase):
    def test_mirrors_match_canon(self):
        r = subprocess.run([sys.executable, "-I", str(SCRIPT), "--check"],
                           capture_output=True, text=True, encoding="utf-8", cwd=PROJECT_ROOT)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_no_template_markers(self):
        files = list(CANON_RULES.glob("*.md")) + list(CANON_AGENTS.glob("*.md"))
        for d in MIRROR_DIRS:
            files += [p for p in d.glob("*") if p.suffix in (".md", ".toml")]
        self.assertTrue(files)
        for f in files:
            text = f.read_text(encoding="utf-8")
            self.assertIsNone(PLACEHOLDER_RE.search(text), f"{f.relative_to(PROJECT_ROOT)}: маркер шаблонизатора")

    def test_every_rule_names_its_topics(self):
        for f in CANON_RULES.glob("*.md"):
            self.assertIn("Источник для:", f.read_text(encoding="utf-8"), f.name)

    def test_codex_toml_parses(self):
        try:
            import tomllib
        except ModuleNotFoundError:  # Python 3.10
            self.skipTest("tomllib появился в Python 3.11")
        for f in (PROJECT_ROOT / ".codex" / "agents").glob("*.toml"):
            data = tomllib.loads(f.read_text(encoding="utf-8"))
            for key in ("name", "description", "developer_instructions"):
                self.assertTrue(data.get(key), f"{f.name}: нет {key}")


if __name__ == "__main__":
    unittest.main()
