"""ADK agent that answers questions about books using the MCP server."""
import os

from fastapi import FastAPI
from pydantic import BaseModel
from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool.mcp_toolset import (
    McpToolset,
    StreamableHTTPConnectionParams,
)
from google.adk.runners import InMemoryRunner
from google.genai import types


MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://mcp-server:8080/mcp")
MODEL = os.getenv("AGENT_MODEL", "gemini-2.5-flash")

INSTRUCTION = """You are a helpful assistant that answers questions about
a small library of books. You have two tools:

- search_books(query, top_k): retrieves the most relevant passages from the
  books for a given query.
- list_books(): returns the titles of all books available.

Always call search_books before answering questions about the books.
Ground your answer in the retrieved passages and cite the source book.
If the retrieved passages don't contain the answer, say so explicitly
instead of making things up.
"""


mcp_toolset = McpToolset(
    connection_params=StreamableHTTPConnectionParams(url=MCP_SERVER_URL)
)

agent = LlmAgent(
    name="books_agent",
    model=MODEL,
    instruction=INSTRUCTION,
    tools=[mcp_toolset],
)

runner = InMemoryRunner(agent=agent, app_name="books")


class Question(BaseModel):
    question: str
    user_id: str = "default"


app = FastAPI(title="Books Agent", version="0.1.0")


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.post("/ask")
async def ask(q: Question):
    session = await runner.session_service.create_session(
        app_name="books", user_id=q.user_id
    )
    content = types.Content(role="user", parts=[types.Part(text=q.question)])

    answer_parts = []
    tool_calls = []
    async for event in runner.run_async(
        user_id=q.user_id, session_id=session.id, new_message=content
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    answer_parts.append(part.text)
                if part.function_call:
                    tool_calls.append({
                        "tool": part.function_call.name,
                        "args": dict(part.function_call.args or {}),
                    })

    return {
        "question": q.question,
        "answer": "".join(answer_parts).strip(),
        "tools_called": tool_calls,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9000)
