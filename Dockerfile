FROM python:3.11-slim

WORKDIR /app

# The app's runtime deps are declared in pyproject.toml — one source of truth.
COPY pyproject.toml README.md ./
COPY backend ./backend
COPY scripts ./scripts
RUN pip install --no-cache-dir .

EXPOSE 8000

# Two entrypoints (override `command` in compose):
#   api    -> uvicorn backend.main:app --host 0.0.0.0 --port 8000
#   worker -> arq backend.job_queue.arq_worker.WorkerSettings
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
