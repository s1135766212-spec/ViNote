import asyncio
import hashlib
import json
import logging
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Form, Request
from pydantic import BaseModel

from backend.services.content_summarizer import ContentSummarizer
from backend.services.media_ingestion import transcribe_local_media, transcribe_remote_media
from backend.core.state import (
    tasks, active_tasks,
    save_tasks, broadcast_task_update, TEMP_DIR,
)
from backend.core.errors import internal_error, task_failure

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


class MindmapRequest(BaseModel):
    content: str
    language: str = "zh"


class TextMindmapTaskRequest(BaseModel):
    content: str
    language: str = "zh"
    request_id: str


def _validate_request_id(request_id: str) -> str:
    try:
        return str(uuid.UUID(request_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=422, detail="request_id 必须是有效的 UUID")


def _request_fingerprint(kind: str, source: str, language: str) -> str:
    payload = json.dumps(
        {"kind": kind, "source": source, "language": language},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _find_mindmap_request(request_id: str, fingerprint: str) -> str | None:
    for task_id, task_data in tasks.items():
        if task_data.get("mindmap_request_id") != request_id:
            continue
        if task_data.get("request_fingerprint") != fingerprint:
            raise HTTPException(
                status_code=409,
                detail="request_id 已用于不同的思维导图输入，请为新请求生成新的 request_id",
            )
        return task_id
    return None


def _start_mindmap_task(
    request_id: str,
    fingerprint: str,
    task_data: dict,
    worker,
    *worker_args,
) -> str:
    existing_id = _find_mindmap_request(request_id, fingerprint)
    if existing_id:
        return existing_id

    task_id = str(uuid.uuid4())
    task_data.update({
        "status": "processing",
        "progress": 0,
        "mindmap": None,
        "error": None,
        "task_type": "mindmap",
        "mindmap_request_id": request_id,
        "request_fingerprint": fingerprint,
    })
    tasks[task_id] = task_data
    save_tasks(tasks)
    active_tasks[task_id] = asyncio.create_task(worker(task_id, *worker_args))
    return task_id


async def _publish_task(task_id: str) -> None:
    task_data = tasks.get(task_id)
    if task_data is None:
        return
    save_tasks(tasks)
    public_data = {key: value for key, value in task_data.items() if key not in {"request_fingerprint", "mindmap_request_id"}}
    await broadcast_task_update(task_id, public_data)


async def _complete_mindmap_task(task_id: str, mindmap: str, **metadata) -> None:
    task_data = tasks.get(task_id)
    if task_data is None:
        return
    if task_data.get("cancel_requested"):
        task_data.update({"status": "cancelled", "progress": 0, "message": "后台任务已确认取消；模型服务或底层工作可能仍在收尾"})
    else:
        task_data.update({
            "status": "completed",
            "progress": 100,
            "message": "✨ 思维导图生成完成！",
            "mindmap": mindmap or "",
            **metadata,
        })
    await _publish_task(task_id)


async def _mark_mindmap_cancelled(task_id: str) -> None:
    task_data = tasks.get(task_id)
    if task_data is None:
        return
    task_data.update({
        "status": "cancelled",
        "progress": 0,
        "message": "后台任务已确认取消；模型服务或底层工作可能仍在收尾",
    })
    await _publish_task(task_id)


async def _mark_mindmap_failed(task_id: str, stage: str, error: Exception) -> None:
    task_data = tasks.get(task_id)
    if task_data is None:
        return
    if task_data.get("cancel_requested"):
        await _mark_mindmap_cancelled(task_id)
        return
    message = f"{stage}失败（{type(error).__name__}），请查看服务端日志后重试"
    task_data.update(task_failure(message))
    await _publish_task(task_id)


@router.post("/text-to-mindmap")
async def generate_text_mindmap(req: TextMindmapTaskRequest):
    """Create or retrieve an idempotent background text-to-mindmap task."""
    content = req.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="内容不能为空")
    if len(content) > 50000:
        raise HTTPException(status_code=400, detail="内容过长，请限制在 50000 字以内")
    request_id = _validate_request_id(req.request_id)
    fingerprint = _request_fingerprint("text", content, req.language)
    task_id = _start_mindmap_task(
        request_id,
        fingerprint,
        {"message": "正在生成思维导图...", "source": "text"},
        _text_to_mindmap_task,
        content,
        req.language,
    )
    return {"task_id": task_id}


async def _text_to_mindmap_task(task_id: str, content: str, language: str):
    try:
        summarizer = ContentSummarizer()
        if not summarizer.is_available():
            raise RuntimeError("AI 服务不可用")
        mindmap = await summarizer.generate_mindmap(content, language)
        if not mindmap:
            raise RuntimeError("模型未返回思维导图内容")
        await _complete_mindmap_task(task_id, mindmap)
    except asyncio.CancelledError:
        logger.info("思维导图文本任务 %s 已收到取消确认", task_id)
        await _mark_mindmap_cancelled(task_id)
    except Exception as e:
        logger.exception("思维导图文本任务 %s 失败", task_id)
        await _mark_mindmap_failed(task_id, "文本思维导图生成", e)
    finally:
        active_tasks.pop(task_id, None)


@router.post("/mindmap-tasks/{task_id}/cancel")
async def cancel_mindmap_task(task_id: str):
    task_data = tasks.get(task_id)
    if task_data is None or task_data.get("task_type") != "mindmap":
        raise HTTPException(status_code=404, detail="思维导图任务不存在")
    if task_data.get("status") in {"completed", "error", "cancelled"}:
        return {key: value for key, value in task_data.items() if key not in {"request_fingerprint", "mindmap_request_id"}}

    worker = active_tasks.get(task_id)
    if worker is None or worker.done():
        task_data.update({
            "status": "error",
            "error": "任务已失效：服务端没有可用的后台 worker，请手动重新生成",
            "message": "任务已失效",
            "cancel_requested": False,
        })
        await _publish_task(task_id)
        return {key: value for key, value in task_data.items() if key not in {"request_fingerprint", "mindmap_request_id"}}

    if not task_data.get("cancel_requested"):
        task_data.update({
            "cancel_requested": True,
            "message": "已请求取消，等待后台任务确认；模型 API 请求可能仍在处理中",
        })
        await _publish_task(task_id)
        worker.cancel()
    return {key: value for key, value in task_data.items() if key not in {"request_fingerprint", "mindmap_request_id"}}


@router.post("/generate-mindmap")
async def generate_mindmap(req: MindmapRequest):
    """接收文本内容，直接调用大模型生成思维导图 Markdown 结构"""
    content = req.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="内容不能为空")

    if len(content) > 50000:
        raise HTTPException(status_code=400, detail="内容过长，请限制在 50000 字以内")

    try:
        summarizer = ContentSummarizer()
        if not summarizer.is_available():
            raise HTTPException(status_code=503, detail="AI 服务不可用")

        mindmap = await summarizer.generate_mindmap(content, req.language)
        if not mindmap:
            raise internal_error("生成思维导图失败")

        return {"mindmap": mindmap}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"生成思维导图失败: {e}")
        raise internal_error("生成思维导图失败")


@router.post("/video-to-mindmap")
async def video_to_mindmap(
    url: str = Form(...),
    language: str = Form(default="zh"),
    request_id: str = Form(...),
):
    """视频 → 下载 → 转录 → 生成思维导图（跳过优化/摘要/翻译）"""
    request_id = _validate_request_id(request_id)
    fingerprint = _request_fingerprint("video", url.strip(), language)

    # 防御：如果收到本地文件路径，自动走本地处理流程
    if os.path.exists(url) and os.path.isfile(url):
        from backend.utils.file_handler import MEDIA_EXTENSIONS
        file_ext = Path(url).suffix.lower()
        if file_ext in MEDIA_EXTENSIONS:
            task_id = _start_mindmap_task(
                request_id,
                fingerprint,
                {"message": "开始处理本地文件...", "source": "local_path", "file_path": url},
                _local_video_to_mindmap_task,
                url,
                language,
            )
            return {"task_id": task_id}

    task_id = _start_mindmap_task(
        request_id,
        fingerprint,
        {"message": "开始处理...", "url": url},
        _video_to_mindmap_task,
        url,
        language,
    )
    return {"task_id": task_id}


async def _video_to_mindmap_task(task_id: str, url: str, language: str):
    try:
        async def progress(pct: int, msg: str):
            task_data = tasks.get(task_id)
            if task_data is None or task_data.get("cancel_requested"):
                raise asyncio.CancelledError
            task_data.update({"progress": pct, "message": msg})
            await _publish_task(task_id)

        async def on_stage(stage: str):
            mapped = {
                "checking_subtitles": (5, "📄 正在检查视频字幕..."),
                "subtitle_ready": (40, "✅ 已从字幕中提取文本，跳过音频下载"),
                "downloading_audio": (10, "🎬 无可用字幕，正在下载音频..."),
                "transcribing_audio": (30, "🎤 正在转录音频..."),
            }.get(stage)
            if mapped:
                await progress(*mapped)

        result = await transcribe_remote_media(url, TEMP_DIR, on_stage, include_metadata=True)

        await progress(80, "🧠 正在生成思维导图...")
        summarizer = ContentSummarizer()
        mindmap = await summarizer.generate_mindmap(result.transcript, language)

        await _complete_mindmap_task(task_id, mindmap, video_title=result.video_title)

    except asyncio.CancelledError:
        logger.info(f"思维导图任务 {task_id} 被取消")
        await _mark_mindmap_cancelled(task_id)

    except Exception as e:
        logger.exception("思维导图任务 %s 失败", task_id)
        await _mark_mindmap_failed(task_id, "视频思维导图生成", e)

    finally:
        active_tasks.pop(task_id, None)


@router.post("/local-video-to-mindmap")
async def local_video_to_mindmap(request: Request):
    """本地视频文件 → 提取音频 → 转录 → 生成思维导图"""
    try:
        data = await request.json()
        file_path = data.get("file_path", "").strip()
        language = data.get("language", "zh")

        if not file_path:
            raise HTTPException(status_code=400, detail="文件路径不能为空")
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail=f"文件不存在: {file_path}")
        if not os.path.isfile(file_path):
            raise HTTPException(status_code=400, detail="路径不是有效的文件")

        from backend.utils.file_handler import MEDIA_EXTENSIONS
        file_ext = Path(file_path).suffix.lower()
        if file_ext not in MEDIA_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"不支持的文件格式: {file_ext}")

        task_id = str(uuid.uuid4())
        tasks[task_id] = {
            "status": "processing",
            "progress": 0,
            "message": "开始处理本地文件...",
            "mindmap": None,
            "error": None,
            "source": "local_path",
            "file_path": file_path,
        }
        save_tasks(tasks)

        task = asyncio.create_task(_local_video_to_mindmap_task(task_id, file_path, language))
        active_tasks[task_id] = task

        return {"task_id": task_id, "message": "本地文件思维导图任务已创建"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"处理本地路径时出错: {str(e)}")
        raise internal_error("本地思维导图任务创建失败")


async def _local_video_to_mindmap_task(task_id: str, file_path: str, language: str):
    try:
        async def progress(pct: int, msg: str):
            task_data = tasks.get(task_id)
            if task_data is None or task_data.get("cancel_requested"):
                raise asyncio.CancelledError
            task_data.update({"progress": pct, "message": msg})
            await _publish_task(task_id)

        async def on_stage(stage: str):
            mapped = {
                "checking_subtitles": (3, "📄 正在检查内嵌字幕..."),
                "subtitle_ready": (40, "✅ 发现内嵌字幕，跳过音频转录"),
                "extracting_audio": (5, "正在提取音频..."),
                "transcribing_audio": (30, "🎤 正在转录音频..."),
            }.get(stage)
            if mapped:
                await progress(*mapped)

        result = await transcribe_local_media(
            file_path, TEMP_DIR, task_id, on_stage, include_metadata=True
        )

        await progress(80, "🧠 正在生成思维导图...")
        summarizer = ContentSummarizer()
        mindmap = await summarizer.generate_mindmap(result.transcript, language)

        await _complete_mindmap_task(task_id, mindmap, video_title=result.video_title)

    except asyncio.CancelledError:
        logger.info(f"本地思维导图任务 {task_id} 被取消")
        await _mark_mindmap_cancelled(task_id)

    except Exception as e:
        logger.exception("本地思维导图任务 %s 失败", task_id)
        await _mark_mindmap_failed(task_id, "本地视频思维导图生成", e)

    finally:
        active_tasks.pop(task_id, None)
