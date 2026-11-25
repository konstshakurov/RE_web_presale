# Ai_RE_Project. Authors: Konstantin Shakurov, Andrey Gerasimov
"""Regression tests: phase.py must generate a LAUNCHABLE server config.

The generated .mcp.json runs `python <abs path to skills/xxx_mcp.py>`.
In script mode Python puts the SCRIPT's directory (skills/) on sys.path —
not the project root and not the cwd — so `from skills.common import ...`
inside every server died with `ModuleNotFoundError: No module named
'skills'` on any machine, regardless of where the client launched it.
The unit suite never saw it because conftest mocks the mcp package and
imports modules in-process, where the root IS on sys.path.

Fix under test: _server() ships env.PYTHONPATH = project root.
"""

import json
import os
import subprocess
import sys

import phase


def test_server_config_carries_pythonpath():
    cfg = phase._server("skills/planning_mcp.py")
    assert cfg.get("env", {}).get("PYTHONPATH") == str(phase.PROJECT_ROOT)


def test_every_phase_server_carries_pythonpath():
    for phase_name, ph in phase.PHASES.items():
        for server_name, cfg in ph["servers"].items():
            assert cfg.get("env", {}).get("PYTHONPATH") == str(phase.PROJECT_ROOT), (
                f"{phase_name}/{server_name} is generated without PYTHONPATH "
                "and cannot resolve the skills package when launched"
            )


def test_script_mode_launch_resolves_skills_package():
    """Launch a server exactly the way .mcp.json does.

    A missing external dependency (`No module named 'mcp'` on hosts without
    the real package) is an install concern and is tolerated here; the path
    defect (`No module named 'skills'`) is ours and is not.
    """
    cfg = phase._server("skills/planning_mcp.py")
    env = {**os.environ, **cfg.get("env", {})}
    proc = subprocess.run(
        [sys.executable, *cfg["args"]],
        input="",
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        cwd=str(phase.PROJECT_ROOT),
        check=False,
    )
    assert "No module named 'skills'" not in proc.stderr


def test_opencode_server_uses_local_config_shape():
    server = phase._server("skills/requirements_assess_changes_mcp.py")
    opencode_server = phase._as_opencode_server(server)

    assert opencode_server == {
        "type": "local",
        "command": [
            "python",
            str(phase.PROJECT_ROOT / "skills/requirements_assess_changes_mcp.py"),
        ],
        "environment": {"PYTHONPATH": str(phase.PROJECT_ROOT)},
    }


def test_phase_config_preserves_unmanaged_settings_and_filters_servers():
    config = {
        "$schema": "https://opencode.ai/config.json",
        "model": "test/model",
        "mcp": {
            "timeout": {"startup": 45000},
            "servers": {
                "external-tools": {"type": "local", "command": ["external"]},
                "babok-ch7-71": {"type": "local", "command": ["stale"]},
            },
        },
    }

    actual = phase.opencode_config_for_phase(config, "lifecycle")

    assert actual["model"] == "test/model"
    assert actual["mcp"]["timeout"] == {"startup": 45000}
    assert actual["mcp"]["servers"]["external-tools"] == config["mcp"]["servers"]["external-tools"]
    assert set(actual["mcp"]["servers"]) == {
        "external-tools",
        *phase.PHASES["lifecycle"]["servers"],
    }
    assert "babok-ch5-54" in actual["mcp"]["servers"]
    assert "babok-ch7-71" not in actual["mcp"]["servers"]
    assert "babok-ch7-71" in config["mcp"]["servers"]


def test_write_phase_updates_both_clients_and_preserves_custom_config(tmp_path, monkeypatch):
    config_path = tmp_path / ".opencode" / "opencode.json"
    config_path.parent.mkdir()
    config_path.write_text(
        json.dumps({
            "$schema": "https://opencode.ai/config.json",
            "mcp": {
                "servers": {
                    "external-tools": {"type": "local", "command": ["external"]}
                }
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(phase, "OPENCODE_CONFIG_FILE", config_path)
    monkeypatch.setattr(phase, "MCP_FILE", tmp_path / ".mcp.json")
    monkeypatch.setattr(phase, "STATE_FILE", tmp_path / ".phase_state")

    phase.write_phase("lifecycle")

    opencode = json.loads(config_path.read_text(encoding="utf-8"))
    claude = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
    assert "babok-ch5-54" in opencode["mcp"]["servers"]
    assert "external-tools" in opencode["mcp"]["servers"]
    assert "babok-ch7-71" not in opencode["mcp"]["servers"]
    assert set(claude["mcpServers"]) == set(phase.PHASES["lifecycle"]["servers"])
    assert (tmp_path / ".phase_state").read_text(encoding="utf-8") == "lifecycle"
