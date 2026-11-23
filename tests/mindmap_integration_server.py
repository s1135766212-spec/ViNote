"""Isolated real ViNote app with only external mind-map generation replaced.

Run inside a disposable container without mounts for application data.
"""
import asyncio
import json
import os

os.environ["OPENAI_API_KEY"] = ""
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

from fastapi import Request
from backend.main import app
from backend.core import state
from backend.services.content_summarizer import ContentSummarizer

model_calls = 0
release_event = asyncio.Event()
outcome = "completed"


async def fake_generate_mindmap(self, content, target_language="zh"):
    global model_calls
    model_calls += 1
    await release_event.wait()
    if outcome == "error":
        raise RuntimeError("MOCK_FAILURE: controlled model error")
    return "# INTEGRATION_MOCK_RESULT\n- " + content[:60]


ContentSummarizer.is_available = lambda self: True
ContentSummarizer.generate_mindmap = fake_generate_mindmap


@app.post("/__test__/configure")
async def configure(request: Request):
    global release_event, outcome
    payload = await request.json()
    outcome = payload["outcome"]
    if outcome not in {"completed", "error"}:
        raise ValueError("invalid mock outcome")
    release_event = asyncio.Event()
    return {"outcome": outcome}


@app.post("/__test__/release")
async def release():
    release_event.set()
    return {"released": True}


@app.post("/__test__/stats")
async def stats():
    stored = json.loads(state.TASKS_FILE.read_text(encoding="utf-8")) if state.TASKS_FILE.exists() else {}
    return {
        "model_calls": model_calls,
        "tasks": {
            task_id: {"status": task.get("status"), "has_mindmap": bool(task.get("mindmap"))}
            for task_id, task in state.tasks.items()
            if task.get("task_type") == "mindmap"
        },
        "saved": {
            task_id: {"status": task.get("status"), "has_mindmap": bool(task.get("mindmap"))}
            for task_id, task in stored.items()
            if task.get("task_type") == "mindmap"
        },
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8999, log_level="warning")
