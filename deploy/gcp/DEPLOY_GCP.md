# Deploying to Google Cloud (GCP)

The production stack is Dockerized: `api` (FastAPI/uvicorn) + `worker` (arq)
+ `redis` + `postgres` + `caddy` (auto-HTTPS reverse proxy). One Compute
Engine VM runs it all — ~$15-25/month, no Kubernetes needed.

```
GitHub ──webhook──▶ https://your-domain/webhook/github
                         │
                      caddy (HTTPS)
                         │
                      api :8000 ──claim──▶ postgres
                         │ enqueue
                      redis ◀── arq worker ──▶ postgres
                         │
                      (LLM API outbound)
```

## 0. Prerequisites

- `gcloud` CLI installed + authenticated (`gcloud auth login`)
- A domain you control (for HTTPS) — or use the VM's external IP with HTTP
  for a quick trial (GitHub webhooks prefer HTTPS; the IP works but shows a
  warning).
- Your app secrets: `OPENAI_API_KEY`, `GITHUB_WEBHOOK_SECRET`,
  `GOVERNANCE_API_KEY`, `GITHUB_TOKEN` (repo read — the worker fetches diffs).

## 1. Project + static IP

```bash
gcloud config set project <your-project-id>
gcloud compute addresses create pr-review-ip --region <region> --global
gcloud compute addresses describe pr-review-ip --global --format="value(address)"
# -> 34.xxx.xxx.xxx  (note this)
```

## 2. VM

```bash
gcloud compute instances create pr-review-vm \
  --machine-type e2-small \
  --image-family ubuntu-2404-lts --image-project ubuntu-os-cloud \
  --zone <zone> \
  --address pr-review-ip \
  --tags pr-review-web \
  --boot-disk-size 20GB

# allow HTTP/HTTPS from anywhere, SSH from your IP
gcloud compute firewall-rules create allow-web \
  --allow tcp:80,tcp:443 --target-tags pr-review-web

# SSH in
gcloud compute ssh pr-review-vm
```

## 3. Docker on the VM

```bash
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2
sudo usermod -aG docker $USER && newgrp docker
```

## 4. Upload the stack

From your machine:

```bash
# the deploy kit + the migration SQL
gcloud compute scp --recurse Dockerfile backend deploy/gcp pr-review-vm:~/
gcloud compute scp scripts/migrations pr-review-vm:~/deploy/gcp/../../scripts/ 2>/dev/null || true
```

Or simpler — clone the repo on the VM:

```bash
cd ~ && git clone https://github.com/adi9336/AI_PR_REVIEWER.git && cd AI_PR_REVIEWER
cd deploy/gcp
cp .env.example .env && nano .env        # fill ALL secrets
```

## 5. Point your domain at the VM

- Cloud DNS: create an A record `review.example.com → <static-ip>` (or use
  your registrar's DNS).
- Edit `deploy/gcp/Caddyfile`: replace `review.example.com` with your domain.

## 6. Start the stack

```bash
cd deploy/gcp
docker compose up -d --build
docker compose ps          # api, worker, redis, postgres, caddy all healthy
curl https://review.example.com/health
# -> {"status":"ok","database":"connected"}
```

Caddy obtains the Let's Encrypt certificate automatically (ports 80+443 open).

## 7. Wire GitHub

Per repository: Settings → Webhooks → Add webhook

- **Payload URL:** `https://review.example.com/webhook/github`
- **Content type:** `application/json`
- **Secret:** your `GITHUB_WEBHOOK_SECRET` (same as `.env`)
- **Events:** Pull requests

GitHub sends a ping on creation — expect a 200 `pong` delivery. Open a PR and
watch it auto-review: webhook → claim → queue → worker → findings → HITL/escalate.

## 8. Dashboard (optional)

The Next.js dashboard is a dev tool; run it anywhere and point it at the VM:

```bash
cd frontend && cp .env.local.example .env.local
# API_BASE_URL=https://review.example.com
# GOVERNANCE_API_KEY=<same as .env>
npm run build && npm start     # http://localhost:3000
```

## Production upgrades (managed GCP services)

| Piece | Managed option | Swap |
|---|---|---|
| Postgres | **Cloud SQL** (Postgres, small tier) | `TIGER_DATABASE_URL=postgresql://user:pass@<cloudsql-ip>:5432/tiger` (run the migration SQL once) |
| Redis | **Memorystore** | `REDIS_URL=rediss://<memorystore-ip>:6379` |
| Secrets | **Secret Manager** | env_file → `secret-manager` secrets in compose |
| Worker resilience | systemd unit / `docker compose restart` policy is already `always`; the worker reconnects to redis | — |
| TLS | Caddy auto-HTTPS (already) or a **Cloud Load Balancer** + managed cert for multi-region | — |

## Operations

```bash
docker compose logs -f api worker     # tail logs
docker compose restart worker         # after a redis outage (known crash mode)
docker compose up -d --build          # deploy new code
docker compose exec postgres pg_dump -U app tiger > backup.sql   # backup
```

## Costs (approx, single region)

e2-small VM ~ $15/mo · 20GB disk ~ $1/mo · Cloud SQL smallest ~ $10-25/mo
(optional) · Memorystore ~ $15-30/mo (optional). The compose-stack Postgres +
redis on the VM keeps the base around **$16/mo**.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Webhook delivery 401 | `GITHUB_WEBHOOK_SECRET` mismatch between `.env` and the GitHub webhook config |
| Webhook delivery 502/504 | api container down or slow (GitHub times out at 10s): `docker compose logs api` |
| Review stuck `pending` | worker down: `docker compose ps` / `docker compose restart worker` |
| Zero findings on real PRs | `GITHUB_TOKEN` missing/wrong scope → diff fetch fails silently; check `docker compose logs worker` |
| `health` degraded | `TIGER_DATABASE_URL` wrong or postgres not migrated |
| 202 `queued:false` | redis down — claim is durable; start redis or run the review manually |
