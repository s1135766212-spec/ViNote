"""Task recovery and cancellation semantics for mind-map generation."""
import asyncio
import uuid
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException

from backend.core import lifecycle
from backend.routers import mindmap
from backend.routers import tasks as task_routes


class MindMapTaskRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tasks = {}
        self.active_tasks = {}
        self.patches = [
            patch.object(mindmap, "tasks", self.tasks),
            patch.object(mindmap, "active_tasks", self.active_tasks),
            patch.object(mindmap, "save_tasks", Mock()),
            patch.object(mindmap, "broadcast_task_update", AsyncMock()),
            patch.object(mindmap, "TEMP_DIR", SimpleNamespace()),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    async def wait_worker(self, task_id):
        await self.active_tasks[task_id]

    async def test_same_text_request_id_reuses_one_task_and_one_model_call(self):
        model = SimpleNamespace(is_available=lambda: True, generate_mindmap=AsyncMock(return_value="# Result"))
        request_id = str(uuid.uuid4())
        with patch.object(mindmap, "ContentSummarizer", return_value=model):
            first = await mindmap.generate_text_mindmap(
                mindmap.TextMindmapTaskRequest(content="same source", request_id=request_id)
            )
            second = await mindmap.generate_text_mindmap(
                mindmap.TextMindmapTaskRequest(content="same source", request_id=request_id)
            )
            await self.wait_worker(first["task_id"])

        self.assertEqual(first["task_id"], second["task_id"])
        self.assertEqual(model.generate_mindmap.await_count, 1)
        self.assertEqual(self.tasks[first["task_id"]]["status"], "completed")
        self.assertEqual(self.tasks[first["task_id"]]["mindmap"], "# Result")

    async def test_reusing_request_id_with_different_text_is_rejected(self):
        model = SimpleNamespace(is_available=lambda: True, generate_mindmap=AsyncMock(return_value="# Result"))
        request_id = str(uuid.uuid4())
        with patch.object(mindmap, "ContentSummarizer", return_value=model):
            first = await mindmap.generate_text_mindmap(
                mindmap.TextMindmapTaskRequest(content="first source", request_id=request_id)
            )
            with self.assertRaises(HTTPException) as raised:
                await mindmap.generate_text_mindmap(
                    mindmap.TextMindmapTaskRequest(content="different source", request_id=request_id)
                )
            await self.wait_worker(first["task_id"])

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(model.generate_mindmap.await_count, 1)

    async def test_same_video_request_id_does_not_schedule_a_second_worker(self):
        request_id = str(uuid.uuid4())
        with patch.object(mindmap, "_video_to_mindmap_task", new=AsyncMock()) as worker:
            first = await mindmap.video_to_mindmap(url="https://example.test/video", language="zh", request_id=request_id)
            second = await mindmap.video_to_mindmap(url="https://example.test/video", language="zh", request_id=request_id)
            await self.wait_worker(first["task_id"])

        self.assertEqual(first["task_id"], second["task_id"])
        self.assertEqual(worker.await_count, 1)

    async def test_cancel_is_pending_until_worker_acknowledges_and_discards_late_result(self):
        started = asyncio.Event()

        async def return_late_result(*_args):
            started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                return "# Late result"

        model = SimpleNamespace(is_available=lambda: True, generate_mindmap=AsyncMock(side_effect=return_late_result))
        request_id = str(uuid.uuid4())
        with patch.object(mindmap, "ContentSummarizer", return_value=model):
            created = await mindmap.generate_text_mindmap(
                mindmap.TextMindmapTaskRequest(content="source", request_id=request_id)
            )
            task_id = created["task_id"]
            await started.wait()
            pending = await mindmap.cancel_mindmap_task(task_id)
            self.assertEqual(pending["status"], "processing")
            self.assertTrue(pending["cancel_requested"])
            await self.wait_worker(task_id)

        self.assertEqual(self.tasks[task_id]["status"], "cancelled")
        self.assertIsNone(self.tasks[task_id].get("mindmap"))

    async def test_terminal_task_is_not_cancelled_again(self):
        task_id = str(uuid.uuid4())
        self.tasks[task_id] = {"task_type": "mindmap", "status": "completed", "progress": 100, "mindmap": "# Done"}
        result = await mindmap.cancel_mindmap_task(task_id)
        self.assertEqual(result["status"], "completed")
        self.assertNotIn("cancel_requested", result)

    async def test_service_restart_marks_work_interrupted_instead_of_running(self):
        task_map = {
            "map-task": {"task_type": "mindmap", "status": "processing", "cancel_requested": True},
            "done-task": {"task_type": "mindmap", "status": "completed"},
        }
        changed = lifecycle.mark_interrupted_tasks(task_map)
        self.assertTrue(changed)
        self.assertEqual(task_map["map-task"]["status"], "error")
        self.assertFalse(task_map["map-task"]["cancel_requested"])
        self.assertIn("\u4e2d\u65ad", task_map["map-task"]["error"])
        self.assertEqual(task_map["done-task"]["status"], "completed")

    async def test_closing_sse_stream_does_not_cancel_the_background_task(self):
        task_id = str(uuid.uuid4())
        async def wait_forever():
            await asyncio.Future()

        background = asyncio.create_task(wait_forever())
        task_map = {task_id: {"task_type": "mindmap", "status": "processing", "progress": 25}}
        connections = {}
        patches = [
            patch.object(task_routes, "tasks", task_map),
            patch.object(task_routes, "sse_connections", connections),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        async def cancel_background():
            background.cancel()
            await asyncio.gather(background, return_exceptions=True)

        self.addAsyncCleanup(cancel_background)

        response = await task_routes.task_stream(task_id)
        stream = response.body_iterator
        await anext(stream)
        await stream.aclose()

        self.assertFalse(background.cancelled())
        self.assertEqual(task_map[task_id]["status"], "processing")


if __name__ == "__main__":
    unittest.main()
