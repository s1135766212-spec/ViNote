"""Real frontend + real backend MindMap integration against isolated test server.

Only the external model method is replaced by mindmap_integration_server.py.
"""
import os
import re
import time

from playwright.sync_api import expect, sync_playwright

BASE = os.getenv("VINOTE_INTEGRATION_BASE", "http://127.0.0.1:18998")
SESSION_KEY = "vinote.mindmap.session.v1"
SOURCE = "用于前后端联动测试的固定文本，讨论 SQL 左连接。"


def stats(page):
    response = page.request.post(BASE + "/__test__/stats")
    assert response.ok, response.status
    return response.json()


def wait_until(predicate, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = predicate()
        if result:
            return result
        time.sleep(0.1)
    raise AssertionError("timed out waiting for backend state")


def task_id(page):
    return page.evaluate(
        "key => JSON.parse(sessionStorage.getItem(key)).taskId",
        SESSION_KEY,
    )


def configure(page, outcome):
    response = page.request.post(BASE + "/__test__/configure", data={"outcome": outcome})
    assert response.ok, response.text()


def release(page):
    response = page.request.post(BASE + "/__test__/release")
    assert response.ok, response.text()


def submit(page):
    page.goto(BASE + "/mindmap")
    page.get_by_placeholder(re.compile("粘贴任意文本内容")).fill(SOURCE)
    with page.expect_request("**/api/text-to-mindmap") as captured:
        page.get_by_role("button", name="生成思维导图", exact=True).click()
    request_payload = captured.value.post_data_json
    identifier = wait_until(lambda: task_id(page))
    expect(page.get_by_role("button", name="生成中...")).to_be_visible()
    return identifier, request_payload


def leave(page):
    page.get_by_role("link", name="视频笔记", exact=True).click()
    page.wait_for_url("**/note")


def return_to_tool(page):
    page.get_by_role("link", name=re.compile("思维导图")).click()
    page.wait_for_url("**/mindmap")


def wait_status(page, identifier, expected):
    def read():
        response = page.request.get(BASE + "/api/task-status/" + identifier)
        assert response.ok, response.text()
        payload = response.json()
        return payload if payload["status"] == expected else None
    return wait_until(read)


def check_saved(page, identifier, expected, has_mindmap):
    snapshot = stats(page)
    assert snapshot["tasks"][identifier] == {
        "status": expected, "has_mindmap": has_mindmap,
    }
    assert snapshot["saved"][identifier] == {
        "status": expected, "has_mindmap": has_mindmap,
    }
    return snapshot


def run():
    with sync_playwright() as playwright:
        channel = os.getenv("VINOTE_BROWSER_CHANNEL", "chrome")
        browser = playwright.chromium.launch(
            headless=True, **({"channel": channel} if channel != "chromium" else {}),
        )

        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        configure(page, "completed")
        before = stats(page)["model_calls"]
        completed_id, _ = submit(page)
        wait_until(lambda: stats(page)["model_calls"] == before + 1)
        leave(page)
        release(page)
        result = wait_status(page, completed_id, "completed")
        assert "INTEGRATION_MOCK_RESULT" in result["mindmap"]
        return_to_tool(page)
        expect(page.locator(".markmap-wrap")).to_contain_text("INTEGRATION_MOCK_RESULT")
        assert task_id(page) == completed_id
        assert check_saved(page, completed_id, "completed", True)["model_calls"] == before + 1
        print("PASS: leave during generation, complete, return to same saved task")
        page.close()

        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        configure(page, "error")
        before = stats(page)["model_calls"]
        failed_id, _ = submit(page)
        wait_until(lambda: stats(page)["model_calls"] == before + 1)
        leave(page)
        release(page)
        wait_status(page, failed_id, "error")
        return_to_tool(page)
        expect(page.get_by_role("alert")).to_contain_text("文本思维导图生成失败")
        assert task_id(page) == failed_id
        assert check_saved(page, failed_id, "error", False)["model_calls"] == before + 1
        print("PASS: model failure while away, return to same saved error")
        page.close()

        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        configure(page, "completed")
        before = stats(page)
        first_id, payload = submit(page)
        wait_until(lambda: stats(page)["model_calls"] == before["model_calls"] + 1)
        duplicate = page.request.post(BASE + "/api/text-to-mindmap", data=payload)
        assert duplicate.ok, duplicate.text()
        assert duplicate.json()["task_id"] == first_id
        current = stats(page)
        assert current["model_calls"] == before["model_calls"] + 1
        assert len(current["tasks"]) == len(before["tasks"]) + 1
        release(page)
        wait_status(page, first_id, "completed")
        assert check_saved(page, first_id, "completed", True)["model_calls"] == before["model_calls"] + 1
        print("PASS: duplicate request_id reaches real backend, reuses task and model call")
        page.close()
        browser.close()


if __name__ == "__main__":
    run()
