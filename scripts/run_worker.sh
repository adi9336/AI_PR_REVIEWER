#!/usr/bin/env bash
# Supervisor for the arq worker — survives redis outages.
# The arq worker process dies when redis is unreachable (observed twice:
# native redis stop, Docker engine crash). This loop restarts it; the
# GCP compose stack achieves the same via `restart: always`.
# Usage: bash scripts/run_worker.sh   (or run via a service manager)
set -u
cd "$(dirname "$0")/.."
PY=./.venv/Scripts/python.exe
[ -x "$PY" ] || PY=python
while true; do
  echo "[run_worker] starting arq worker $(date +%H:%M:%S)"
  "$PY" -m arq backend.job_queue.arq_worker.WorkerSettings
  echo "[run_worker] worker exited ($?) — restarting in 5s"
  sleep 5
done
