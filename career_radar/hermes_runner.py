"""Small process boundary around the unmodified Hermes Agent checkout."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    request = json.load(sys.stdin)
    hermes_path = Path(request["hermes_path"]).resolve()
    sys.path.insert(0, str(hermes_path))
    os.environ.setdefault("HERMES_HOME", request["hermes_home"])
    from run_agent import AIAgent

    agent = AIAgent(
        base_url=request["base_url"], api_key=os.environ.get("TOKEN_PLAN_API_KEY", ""),
        provider="custom", api_mode="chat_completions", model=request["model"],
        max_iterations=3, enabled_toolsets=[], quiet_mode=True,
        ephemeral_system_prompt=request["system_prompt"],
        skip_context_files=True, skip_memory=True, skip_background_review=True,
        save_trajectories=False,
    )
    try:
        result = agent.run_conversation(request["prompt"], task_id=request["task_id"])
        payload = json.dumps({"final_response": result.get("final_response", "")}, ensure_ascii=False) + "\n"
        os.write(1, payload.encode())
    finally:
        agent.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
