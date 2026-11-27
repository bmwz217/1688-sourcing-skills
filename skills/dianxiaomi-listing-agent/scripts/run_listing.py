"""Run the bundled listing agent from any working directory."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    skill_root = Path(__file__).resolve().parents[1]
    # Load this bundle, not another installed copy of dianxiaomi_agent.
    sys.path.insert(0, str(skill_root))
    sys.dont_write_bytecode = True
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        from dianxiaomi_agent.agent import main as run_agent
    except ModuleNotFoundError as exc:
        if exc.name != "openpyxl":
            raise
        print(json.dumps({
            "state": False,
            "stage": "dependencies",
            "error": "缺少 openpyxl；请使用当前 Python 安装 scripts/requirements.txt 中的依赖。",
            "requirements_path": str(skill_root / "scripts" / "requirements.txt"),
        }, ensure_ascii=False))
        raise SystemExit(1) from None
    run_agent()


if __name__ == "__main__":
    main()
