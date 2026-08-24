# AI Architect Assignment

A small but complete local AI stack: chat interface, model gateway, cost tracking, RAG knowledge base, and an agent that answers grounded questions about books.

Everything runs from a single `docker compose up` after prerequisites and credentials are in place.

## What runs where

| Service | Port | Purpose |
|---|---|---|
| Open WebUI | 3000 | Chat interface with model picker |
| LiteLLM | 4000 | Model gateway (Ollama + Vertex AI Gemini) |
| Postgres | 5432 (internal) | LiteLLM state, spend logs |
| Usage Extractor | 8000 | Dashboard + JSON API + CSV export |
| MCP Server | 8080 | FastMCP server exposing RAG as tools |
| Agent | 9000 | ADK agent, REST endpoint `/ask` |
| Ollama | 11434 (host) | Local LLM with GPU |

## Requirements

Install on the host before anything else:

- **Docker** with Compose plugin
- **gcloud CLI**, authenticated with a Google account
- **Ollama** installed on the host (see trade-off in `ARCHITECTURE.md`):
```bash
  curl -fsSL https://ollama.com/install.sh | sh
  sudo systemctl edit ollama.service
  # add:
  # [Service]
  # Environment="OLLAMA_HOST=0.0.0.0:11434"
  sudo systemctl daemon-reload
  sudo systemctl restart ollama
  ollama pull llama3.2:3b
```

On GCP:

- A project with billing enabled
- These APIs enabled: `aiplatform.googleapis.com`, `storage.googleapis.com`, `iamcredentials.googleapis.com`, `cloudresourcemanager.googleapis.com`, `vectorsearch.googleapis.com`
- A service account with roles: `Agent Platform User`, `Storage Admin`, `Service Usage Consumer`
- A downloaded JSON key for that service account (kept outside the repo)

## Setup

1. Clone the repo and enter it:
```bash
   git clone https://github.com/alexITanasa/ai-architect-assignment.git
   cd ai-architect-assignment
```

2. Copy the env template and fill in credentials:
```bash
   cp .env.example .env
```
   Edit `.env` and set:
   - `GCP_PROJECT_ID` — your project id
   - `GCP_LOCATION=us-central1`
   - `GCP_KEY_HOST_PATH` — absolute path to your service account JSON key on the host
   - `GOOGLE_APPLICATION_CREDENTIALS=/gcp/gcp-key.json` (this is the in-container path, don't change)
   - `LITELLM_MASTER_KEY` — any random string starting with `sk-`
   - `WEBUI_SECRET_KEY` — any random string
   - Postgres user/password/db (any values, must match between `POSTGRES_*` and `DATABASE_URL`)
   - `RAG_CORPUS_NAME` and `GCS_BUCKET` — leave placeholder for now, we'll fill in after RAG ingest

3. Make sure no stale env vars from your shell shadow the ones in `.env`:
```bash
   unset GOOGLE_APPLICATION_CREDENTIALS
```

4. Switch RAG Engine to Serverless mode (one-time, per project):
```bash
   curl -X PATCH \
     -H "Content-Type: application/json" \
     -H "Authorization: Bearer $(gcloud auth print-access-token)" \
     "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/YOUR_PROJECT_ID/locations/us-central1/ragEngineConfig" \
     -d "{'ragManagedDbConfig': {'serverless': {}}}"
```

## First run

```bash
docker compose up -d
docker compose ps
```

All six containers should be `Up`. Wait ~30 seconds for LiteLLM to run its Prisma migrations against Postgres on the first start.

Quick sanity checks:

```bash
# LiteLLM alive
curl -s http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer YOUR_LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"llama3.2-local","messages":[{"role":"user","content":"hi"}]}'

# Usage Extractor alive
curl -s http://localhost:8000/healthz

# MCP server alive (returns 400 without a session, that's expected)
curl -s http://localhost:8080/mcp

# Agent alive
curl -s http://localhost:9000/healthz
```

## Using it

- **Chat**: open [http://localhost:3000](http://localhost:3000). First visit creates the admin account. Both `llama3.2-local` and `gemini-flash` show up in the model picker.
- **Usage dashboard**: [http://localhost:8000](http://localhost:8000) shows KPIs, per-model / per-user breakdowns, last-7-days summary, recent requests. `GET /api/summary`, `GET /api/usage`, `GET /api/export.csv` for programmatic use.
- **Agent**: ask questions about the books:
```bash
  curl -s -X POST http://localhost:9000/ask \
    -H "Content-Type: application/json" \
    -d '{"question": "How does Sherlock Holmes approach a case?"}'
```

## Populating the RAG corpus

The ingest is a one-time script that lives outside Docker (uses the host's gcloud auth):

```bash
# 1. Create a GCS bucket in the same region
export BUCKET_NAME="YOUR_PROJECT_ID-rag-books"
gcloud storage buckets create gs://${BUCKET_NAME} \
  --location=us-central1 --uniform-bucket-level-access

# 2. Drop PDFs into rag-ingest/books/ (any public-domain books work)
#    Then upload them to the bucket:
gcloud storage cp rag-ingest/books/*.pdf gs://${BUCKET_NAME}/

# 3. Run the ingest
cd rag-ingest
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/gcp-key.json
python ingest.py
```

The script prints the corpus name at the end. Copy it into `.env` as `RAG_CORPUS_NAME`, restart the MCP server:

```bash
docker compose restart mcp-server agent
```

## Troubleshooting

- **Agent returns 500 with `PermissionError: [Errno 13] Permission denied: '/gcp/gcp-key.json'`**
  The container user can't read the key. On the host: `chmod 644 ~/gcp-keys/gcp-key.json`.

- **Agent returns 500 with `DefaultCredentialsError: File /home/... was not found`**
  A shell env var is shadowing `.env`. Run `unset GOOGLE_APPLICATION_CREDENTIALS` and `docker compose up -d --force-recreate agent`.

- **`docker compose ps` shows `litellm` restarting**
  Postgres probably isn't healthy yet. Wait 15 more seconds and check `docker compose logs postgres`.

- **RAG ingest fails with `Spanner mode ... restricted to allowlisted projects`**
  You haven't switched to Serverless mode. See setup step 4.

- **Ollama models don't show up in Open WebUI**
  Ollama isn't reachable from Docker. Check `sudo ss -tlnp | grep 11434` — must be `*:11434`, not `127.0.0.1:11434`. If not, edit the systemd override (see prerequisites).

## Repository layout
.
├── docker-compose.yml one stack, six services
├── .env.example template — copy to .env
├── litellm/ model gateway config
├── usage-extractor/ Python service: pulls /spend/logs, exposes dashboard
├── rag-ingest/ one-shot script to build the RAG corpus
├── mcp-server/ FastMCP server exposing the corpus as tools
├── agent/ ADK agent with REST /ask
├── ARCHITECTURE.md decisions, trade-offs, out-of-scope
└── ANSWERS.md written answers to Part 2


## See also

- [`ARCHITECTURE.md`](./ARCHITECTURE.md) — why the pieces are shaped the way they are.
- [`ANSWERS.md`](./ANSWERS.md) — Part 2 written answers.
