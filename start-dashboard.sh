#!/usr/bin/env bash
set -euo pipefail

# Start the Job-Agent dashboard from the repository root, without needing to
# remember PYTHONPATH or the module command.
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

if [[ -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
  PYTHON="$PROJECT_ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="$(command -v python3)"
else
  echo "Python 3 was not found. Install Python 3.12+ and try again." >&2
  exit 1
fi

export PYTHONPATH="$PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON" -m job_agent.dashboard "$@"
