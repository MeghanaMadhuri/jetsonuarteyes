#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PIDFILE="${ROOT}/.voice-edge.pids"
if [[ ! -f "${PIDFILE}" ]]; then
  echo "no .voice-edge.pids"
  exit 0
fi
while read -r pid; do
  if kill -0 "${pid}" 2>/dev/null; then
    kill "${pid}" 2>/dev/null || true
  fi
done < "${PIDFILE}"
rm -f "${PIDFILE}"
echo "voice-edge services stopped"
