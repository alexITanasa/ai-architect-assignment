# AI Architect Assignment

AI stack: chat interface, model gateway, cost tracking, RAG knowledge base, and an agent that answers grounded questions about books.

The runtime stack starts with a single `docker compose up` after prerequisites and credentials are in place. RAG corpus provisioning is handled through a separate Docker Compose bootstrap profile.

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
| RAG Ingest | bootstrap profile | Containerized Vertex AI RAG corpus ingestion |

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
- `GCS_BUCKET` — bucket used to store the books
- `RAG_CORPUS_NAME` — leave the placeholder for now; fill it after the RAG ingest step

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
  -d '{"ragManagedDbConfig":{"serverless":{}}}'
```

## Populating the RAG corpus

RAG ingestion runs inside Docker through the Compose `bootstrap` profile. No local Python environment or manual `pip install` is required.

```bash
# 1. Create a GCS bucket in the same region
export BUCKET_NAME="YOUR_PROJECT_ID-rag-books"

gcloud storage buckets create gs://${BUCKET_NAME} \
  --location=us-central1 \
  --uniform-bucket-level-access

# 2. Drop PDFs into rag-ingest/books/ (any public-domain books work)
#    Then upload them to the bucket:
gcloud storage cp rag-ingest/books/*.pdf gs://${BUCKET_NAME}/

# 3. Run the containerized RAG ingest
docker compose --profile bootstrap run --build --rm rag-ingest
```

The ingest process creates the corpus if it does not exist, or reuses an existing corpus with the same display name. It then imports the books from GCS using the configured chunking strategy.

The script prints the corpus resource name at the end. Copy it into `.env` as `RAG_CORPUS_NAME`.

Example:

```text
RAG_CORPUS_NAME=projects/YOUR-PROJECT/locations/us-central1/ragCorpora/YOUR-CORPUS-ID
```

## First run

```bash
docker compose up -d
docker compose ps
```

All six runtime containers should be `Up`. Wait ~30 seconds for LiteLLM to run its Prisma migrations against Postgres on the first start.

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

If `RAG_CORPUS_NAME` was changed after the runtime containers were already started, restart the RAG-dependent services:

```bash
docker compose restart mcp-server agent
```

## Troubleshooting

- **Agent returns 500 with `PermissionError: [Errno 13] Permission denied: '/gcp/gcp-key.json'`**

  The container user can't read the key. On the host:

```bash
chmod 644 ~/gcp-keys/gcp-key.json
```

- **Agent returns 500 with `DefaultCredentialsError: File /home/... was not found`**

  A shell env var is shadowing `.env`. Run:

```bash
unset GOOGLE_APPLICATION_CREDENTIALS
docker compose up -d --force-recreate agent
```

- **`docker compose ps` shows `litellm` restarting**

  Postgres probably isn't healthy yet. Wait 15 more seconds and check:

```bash
docker compose logs postgres
```

- **RAG ingest fails with `Spanner mode ... restricted to allowlisted projects`**

  You haven't switched to Serverless mode. See setup step 4.

- **Ollama models don't show up in Open WebUI**

  Ollama isn't reachable from Docker. Check:

```bash
sudo ss -tlnp | grep 11434
```

It must be listening on `*:11434`, not `127.0.0.1:11434`. If not, edit the systemd override described in the requirements section.

## Repository layout

```text
.
├── docker-compose.yml
├── .env.example
├── litellm/
├── usage-extractor/
├── rag-ingest/
├── mcp-server/
├── agent/
├── ARCHITECTURE.md
└── ANSWERS.md
```

- `docker-compose.yml` — runtime stack plus the RAG bootstrap profile
- `.env.example` — environment variable template
- `litellm/` — model gateway configuration
- `usage-extractor/` — Python service that pulls `/spend/logs` and exposes usage reporting
- `rag-ingest/` — containerized bootstrap process for the Vertex AI RAG corpus
- `mcp-server/` — FastMCP server exposing the corpus as tools
- `agent/` — ADK agent with REST `/ask`
- `ARCHITECTURE.md` — decisions, trade-offs, and out-of-scope items
- `ANSWERS.md` — written answers to Part 2

## See also

- [`ARCHITECTURE.md`](./ARCHITECTURE.md) — why the pieces are shaped the way they are.
- [`ANSWERS.md`](./ANSWERS.md) — Part 2 written answers.
