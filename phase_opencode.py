#!/usr/bin/env python3
"""
Bridge: phase switch (Claude Code) -> OpenCode.

OpenCode does not read the project `.mcp.json` that `phase.py` generates.
After switching the phase with:
    python phase.py <phase>
run:
    python phase_opencode.py
It converts the current `.mcp.json` into `.opencode/opencode.json` (V2 format),
so OpenCode starts the servers of the active BABOK phase.

After that, restart the OpenCode session (new session / reconnect),
because MCP servers are read only at session startup.
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SRC = PROJECT_ROOT / ".mcp.json"
DST = PROJECT_ROOT / ".opencode" / "opencode.json"


def main() -> int:
    if not SRC.exists():
        print("ERROR: .mcp.json not found — first run: python phase.py <phase>")
        return 1

    src = json.loads(SRC.read_text(encoding="utf-8"))
    mcp_src = src.get("mcpServers", {})

    servers = {}
    for name, cfg in mcp_src.items():
        command = [cfg.get("command", "python"), *cfg.get("args", [])]
        entry = {"type": "local", "command": command}
        env = cfg.get("env") or {}
        if env:
            entry["environment"] = env
        servers[name] = entry

    DST.parent.mkdir(parents=True, exist_ok=True)
    config = {"$schema": "https://opencode.ai/config.json", "mcp": {"servers": servers}}
    DST.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    names = ", ".join(servers) if servers else "(none)"
    print(f"OK: {len(servers)} servers written to {DST}")
    print(f"Servers: {names}")
    print("Now restart the OpenCode session — servers load at session start.")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    sys.exit(main())
