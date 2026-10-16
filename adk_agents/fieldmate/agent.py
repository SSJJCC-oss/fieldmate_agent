"""Entry point for the ADK web interface (`adk web adk_agents`).

`adk web` expects:  <agents_dir>/<agent_name>/agent.py  containing `root_agent`.
Our real agents live in app/agents.py, so this file just points at them —
nothing is duplicated, and WhatsApp, the terminal chat and the web UI all run
the exact same five agents.
"""
import sys
from pathlib import Path

# Make the project root importable so `from app...` works, whatever folder
# `adk web` was started from.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agents import root_agent  # noqa: E402,F401
