# Architecture

## Overview

Six runtime containers plus one process on the host, wired through a single Docker Compose network. `docker compose up` brings the runtime stack alive after `.env` is populated. RAG corpus provisioning is handled separately through a Docker Compose bootstrap profile.

Two logical planes:

- **Chat plane** — the user talks to Open WebUI, everything goes through LiteLLM, cost is tracked in Postgres and mirrored into a local SQLite by the Usage Extractor.
- **RAG plane** — a separate agent reads from the same GCP project through an MCP server that exposes the RAG corpus as tools.

Both planes share the same GCP project, service account, and network. They can be developed and demoed independently.

## Component decisions

### Ollama on the host, not in a container

I run Ollama on the host because:

- The laptop has an NVIDIA RTX 3070 (8 GB VRAM). Passing the GPU into a container works but requires NVIDIA Container Toolkit and adds a second failure mode to debug.
- Ollama already persists models under `~/.ollama`, so a Docker volume would only duplicate what is already durable on the host.
- The rest of the stack still talks to it only through `host.docker.internal:11434` — no `localhost` — so from the Compose network's point of view Ollama is just another endpoint.

Trade-off: a fresh clone needs one extra step (install Ollama, pull the model). Documented in the README as an install requirement.

### PostgreSQL for LiteLLM state, not SQLite or callbacks

LiteLLM's `/spend/logs` endpoint requires a database backend. I considered three options:

1. **PostgreSQL, LiteLLM's native integration.** Chosen. LiteLLM was designed with this in mind — spend tracking, virtual keys, per-user budgets, tag-based routing, all depend on it. Even though this demo only uses spend logs, using the native integration avoids writing something LiteLLM already provides.
2. **Callbacks to a custom endpoint.** Rejected: couples the proxy's availability to the extractor's. If the extractor is down or slow, LiteLLM has to retry or drop requests.
3. **JSON-lines log file.** Rejected: duplicates what LiteLLM already does and makes deduplication awkward.

### SQLite for the Usage Extractor

The extractor keeps its own local store instead of querying Postgres directly. Reasons:

- **Separation of concerns.** LiteLLM's schema is theirs, subject to change; the extractor owns its aggregations. If they change fields, only the ingest code needs updating.
- **Fits the load.** A small internal AI platform's usage table is tens of thousands of rows per day — well within SQLite's comfort zone.
- **Portable.** The whole extractor is a container plus a single file volume.

If the extractor ever needed to serve queries at scale (multi-tenant dashboard, retention windows measured in years), I'd move it to Postgres — same interface, different `db.py`.

### RAG Engine in Serverless mode

By default new GCP projects can't use RAG Engine's Spanner-backed mode in `us-central1` (allowlist only). The alternative is Serverless mode, which is generally available. It uses Vertex AI Vector Search 2.0 under the hood, which is fine for this scale.

Switching modes is a one-time PATCH on `ragEngineConfig`. Documented in the README so the next person doesn't hit the same wall.

### RAG ingestion as a Compose bootstrap profile

RAG ingestion is containerized but kept outside the default runtime startup through a Docker Compose `bootstrap` profile.

The ingest process is a provisioning task rather than a long-running service: it creates or reuses the Vertex AI RAG corpus, imports the source books from GCS, and then exits. Running it on every `docker compose up` would add unnecessary startup time and could repeatedly trigger ingestion work.

Keeping it behind a profile preserves the main runtime flow while still ensuring the ingestion environment is reproducible and does not depend on a locally installed Python environment.

### MCP over HTTP, not stdio

MCP supports both. HTTP is the better fit because:

- The MCP server needs to run continuously alongside the agent, both containerized. HTTP fits Docker Compose naturally; stdio would force the agent to spawn the server as a subprocess and manage its lifecycle.
- Debugging is easier — I can `curl` the MCP endpoint if something goes wrong.
- FastMCP handles establishing and maintaining the HTTP connection between the agent and the server automatically; I don't need to write extra code for that.

### Agent standalone (REST), not integrated in Open WebUI

The agent exposes `POST /ask`. It's not plugged into Open WebUI as a custom model. Why:

- The two chat components (Open WebUI and the agent) demonstrate different capabilities and are easier to reason about separately. Unifying them would hide the fact that the agent uses RAG-grounded tool calls rather than a plain chat completion.
- Integrating a custom tool-using backend into Open WebUI would require either implementing an OpenAI-compatible endpoint that streams tool events, or writing an Open WebUI plugin. Both are meaningful scope creep for a demo that adds no technical value.

## Trade-offs

### Chunk size 512 with 100-token overlap

Good size for narrative text. 512 tokens keeps each chunk semantically dense (a coherent paragraph or two) so embeddings capture a real idea instead of a fragment. 100-token overlap (~20%) prevents cutting sentences at chunk boundaries — important for narrative books where context bleeds across paragraphs.

Alternatives considered: 256 (too fragmented — retrieval scores were unreliable in early tests), 1024 (embeddings become averages of multiple ideas, less useful for distinguishing between queries). If the corpus were code or highly structured (contracts, specs), I'd revisit.

### Gemini 2.5 Flash

I picked 2.5 Flash because:

- The rest of the stack (LiteLLM version, RAG SDK behavior) is already coexisting with several rebranded/renamed APIs. Adding a very-new model version increases the surface area for "which minor SDK bug bit me today."
- 2.5 Flash is fully GA and has more community examples for troubleshooting.

Cost isn't the constraint at this scale.

### Two books, not three (Gibbon dropped)

Uploaded three PDFs, but *Decline and Fall of the Roman Empire* (17 MB) failed embedding on ingest. Rather than debug an isolated ingest failure that doesn't teach anything about the architecture, I dropped it and kept Sherlock Holmes + Sherman's Memoirs. Two books are enough for the agent to demonstrate discrimination (queries about detective work land in Holmes, queries about military leadership land in Sherman).

## Deliberately out of scope

Things I considered but left out:

- **Streaming on the agent endpoint.** The current `/ask` returns the full answer at once. Streaming would add SSE handling on both sides for no gain in what's being demonstrated.
- **Authentication on any endpoint.** Everything is `localhost` and single-user. In a real deployment the agent's `/ask` and the extractor's dashboard would sit behind whatever the org already uses (IAP, Cloudflare Access, an internal SSO).
- **Alerting when spend exceeds a threshold.** Would live in the Usage Extractor. Kept a $5 budget alert on the GCP billing account instead — cheaper and belt-and-braces with Google's own.
- **Data retention / cleanup jobs.** The usage DB grows without bound. For a demo that runs for hours, not months, this doesn't matter.
- **Real-time push from LiteLLM to the extractor.** 60-second polling is fine for a reporting use case. Real-time only pays off when a dashboard needs sub-second freshness.
- **Vector re-ranking on the RAG side.** RAG Engine's default retrieval is already useful for two small books. On a larger corpus I'd add a re-ranker.
- **Multi-turn agent conversations.** Sessions are created fresh per request. Multi-turn adds state management (session store, TTL, per-user isolation) that isn't needed to answer one question at a time.

## What I'd add with more time

In order of value:

1. **Per-user API keys in LiteLLM.** The database is already there. This unlocks per-team budgets and stops "one master key for everything" — the single biggest win for adopting the platform at company scale.
2. **A pgAdmin or Metabase container** wired to the same Postgres, so ops can inspect LiteLLM's tables without needing the extractor. The extractor stays focused on dashboards for humans; ops gets raw SQL.
3. **Integrate the agent as a model in Open WebUI** through an OpenAI-compatible shim so the two chat components merge. Useful for demo, not necessary for the architecture story.
4. **Health checks and readiness probes** across all containers. Compose has some via `healthcheck`; extending them so `depends_on: service_healthy` gates every dependency would make cold starts more deterministic.
5. **A tiny CLI (`ask.py`)** as a second client for the agent, so non-developers can try it without curl.
