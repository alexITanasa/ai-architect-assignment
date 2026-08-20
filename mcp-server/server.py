"""MCP server exposing the Vertex AI RAG corpus as tools."""
import os
import sys

from fastmcp import FastMCP
import vertexai
from vertexai import rag


PROJECT_ID = os.getenv("GCP_PROJECT_ID", "ai-architect-test-506013")
LOCATION = os.getenv("GCP_LOCATION", "us-central1")
CORPUS_NAME = os.getenv("RAG_CORPUS_NAME")
DEFAULT_TOP_K = int(os.getenv("RAG_TOP_K", "5"))

if not CORPUS_NAME:
    print("ERROR: RAG_CORPUS_NAME env var is required", file=sys.stderr)
    sys.exit(1)

vertexai.init(project=PROJECT_ID, location=LOCATION)

mcp = FastMCP("books-rag")


@mcp.tool
def search_books(query: str, top_k: int = DEFAULT_TOP_K) -> dict:
    """Search the books knowledge base for passages relevant to a query.

    Returns the most relevant text chunks with their source document and
    similarity score. Use this to ground answers in the actual book content
    before responding to the user.
    """
    response = rag.retrieval_query(
        rag_resources=[rag.RagResource(rag_corpus=CORPUS_NAME)],
        text=query,
        rag_retrieval_config=rag.RagRetrievalConfig(top_k=top_k),
    )
    results = []
    for ctx in response.contexts.contexts:
        results.append({
            "source": getattr(ctx, "source_display_name", None),
            "score": getattr(ctx, "score", None),
            "text": ctx.text,
        })
    return {"query": query, "results": results}


@mcp.tool
def list_books() -> dict:
    """List all books available in the knowledge base."""
    files = list(rag.list_files(CORPUS_NAME))
    return {
        "count": len(files),
        "books": [f.display_name for f in files],
    }


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8080)
