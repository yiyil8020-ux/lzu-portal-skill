#!/usr/bin/env python3
"""侦察脚本：CDP 监听用户手动操作，抓 API 接口，回放验证，落盘 routes.json。

用法:
    python3 skills/portal/recon.py "查校园卡余额"
    python3 skills/portal/recon.py "查考试安排" --cdp http://127.0.0.1:9222

交互流程:
    1. CDP 连 Chrome，打开 my.lzu.edu.cn
    2. 提示用户在浏览器里手动点目标功能
    3. page.on("response") 监听 *.lzu.edu.cn 的 XHR/Fetch
    4. 打印捕获的 JSON 请求（headers 脱敏），用户多选要记的
    5. 可选翻页 diff（用户点两次，自动找页码字段）
    6. requests 原样回放验证（确认不是临时凭证）
    7. 验证通过 → 写入 routes.json + 导出 storage_state
"""

import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import requests

ROUTES_PATH = Path.home() / ".openclaw-lzu" / "portal_routes.json"
STATE_PATH = Path.home() / ".openclaw-lzu" / "portal_state.json"
PORTAL_HOME = "https://my.lzu.edu.cn/mylzu/home"

# 排除的静态资源扩展名
STATIC_EXTS = (".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".woff", ".woff2", ".ico", ".svg", ".mp4", ".ttf")
# WAF challenge URL 特征
WAF_PATTERNS = ("ofolQLH6ypbp", "$_ts", "nuzTFG35Hou7")
# 脱敏的 header key（小写匹配）
SENSITIVE_HEADERS = ("authorization", "cookie", "x-token", "x-sc-od", "set-cookie")


def mask_sensitive(headers: dict) -> dict:
    """脱敏：敏感 header 只显示末 4 位。"""
    masked = {}
    for k, v in headers.items():
        kl = k.lower()
        if kl in SENSITIVE_HEADERS and v:
            masked[k] = f"****{str(v)[-4:]}" if len(str(v)) > 4 else "****"
        else:
            masked[k] = v
    return masked


def should_capture(url: str, resource_type: str) -> bool:
    """过滤规则：只留 *.lzu.edu.cn 的 XHR/Fetch JSON 请求。"""
    if "lzu.edu.cn" not in url:
        return False
    if resource_type not in ("xhr", "fetch", "document"):
        return False
    if any(url.endswith(ext) for ext in STATIC_EXTS):
        return False
    if any(p in url for p in WAF_PATTERNS):
        return False
    # 排除纯页面入口 (.html/.htm/.jsp)，但保留 .do/.json/无扩展
    lower = url.lower().split("?")[0]
    if lower.endswith((".html", ".htm", ".jsp")):
        return False
    return True


def extract_params(method: str, url: str, post_data: str | None) -> dict:
    """提取 GET query params 或 POST body params。"""
    if method == "GET":
        return {k: v[0] if len(v) == 1 else v for k, v in parse_qs(urlparse(url).query).items()}
    if post_data:
        # 尝试 JSON
        try:
            return json.loads(post_data)
        except Exception:
            pass
        # 尝试 form-urlencoded
        try:
            return {k: v[0] if len(v) == 1 else v for k, v in parse_qs(post_data).items()}
        except Exception:
            pass
    return {}


def load_routes() -> dict:
    if ROUTES_PATH.exists():
        return json.loads(ROUTES_PATH.read_text("utf-8"))
    return {}


def save_routes(routes: dict):
    ROUTES_PATH.parent.mkdir(parents=True, exist_ok=True)
    ROUTES_PATH.write_text(json.dumps(routes, ensure_ascii=False, indent=2), "utf-8")


def load_state_cookies() -> dict:
    """从 storage_state 读 cookie dict。"""
    if not STATE_PATH.exists():
        return {}
    state = json.loads(STATE_PATH.read_text("utf-8"))
    return {c["name"]: c["value"] for c in state.get("cookies", []) if "lzu.edu.cn" in c.get("domain", "")}


def replay_verify(captured_item: dict, cookies: dict) -> tuple[bool, str]:
    """用 requests 原样打一遍，确认能拿到同样数据。返回 (ok, message)。"""
    url = captured_item["url"]
    method = captured_item["method"]
    params = captured_item.get("params", {})
    headers_extra = {k: v for k, v in captured_item["headers"].items()
                     if k.lower() not in SENSITIVE_HEADERS and k.lower() not in ("host", "content-length")}
    headers_extra["User-Agent"] = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                   "AppleWebKit/537.36 Chrome/149.0.0.0 Safari/537.36")
    try:
        if method == "GET":
            r = requests.get(url, params=params, headers=headers_extra,
                             cookies=cookies, timeout=15, allow_redirects=False)
        else:
            r = requests.post(url, data=params, headers=headers_extra,
                              cookies=cookies, timeout=15, allow_redirects=False)
        if r.status_code == 412:
            return False, "412 WAF 拦截（API 也走 WAF，建议标 cdp_only）"
        if r.status_code in (401, 302):
            return False, f"{r.status_code} cookie 过期或未授权"
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}"
        try:
            body = r.json()
        except Exception:
            return False, "响应非 JSON"
        return True, json.dumps(body, ensure_ascii=False)[:200]
    except requests.Timeout:
        return False, "请求超时"
    except Exception as e:
        return False, str(e)


def diff_params(p1: dict, p2: dict) -> list[str]:
    """diff 两次请求的参数，返回变化的 key 列表（可能是页码/偏移量）。"""
    changed = []
    all_keys = set(p1.keys()) | set(p2.keys())
    for k in all_keys:
        if p1.get(k) != p2.get(k):
            changed.append(k)
    return changed


def run_recon(task_name: str, cdp_url: str = "http://127.0.0.1:9222"):
    """主侦察流程。"""
    from playwright.sync_api import sync_playwright

    captured: list[dict] = []

    def on_response(response):
        try:
            url = response.url
            req = response.request
            if not should_capture(url, req.resource_type):
                return
            try:
                body = response.json()
            except Exception:
                return  # 非 JSON 不记
            params = extract_params(req.method, url, req.post_data)
            # 去掉 URL 里的 query（params 已提取）
            clean_url = url.split("?")[0] if req.method == "POST" else url
            captured.append({
                "url": url.split("?")[0] if req.method == "POST" else url,
                "full_url": url,
                "method": req.method,
                "params": params,
                "headers": mask_sensitive(dict(req.headers)),
                "status": response.status,
                "response_body": body,
                "response_preview": json.dumps(body, ensure_ascii=False)[:300],
            })
        except Exception:
            pass

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cdp_url)
        ctx = browser.contexts[0]

        # 找或创建门户 tab
        page = None
        for pg in ctx.pages:
            if "lzu.edu.cn" in pg.url:
                page = pg
                break
        if not page:
            page = ctx.new_page()
            page.goto(PORTAL_HOME, wait_until="domcontentloaded")

        page.on("response", on_response)

        # 导航到首页确保干净状态
        if "/mylzu/home" not in page.url:
            page.goto(PORTAL_HOME, wait_until="domcontentloaded")

        print(f"\n{'='*60}")
        print(f"侦察任务: {task_name}")
        print(f"{'='*60}")
        print(f"\n浏览器已连上: {page.url}")
        print(f"title: {page.title()!r}")
        print(f"\n请在浏览器里操作 —— 点一下你要侦察的功能（比如「{task_name}」）。")
        print(f"我在后台监听网络请求。操作完按 Enter 继续...")
        print(f"（如果要侦察翻页功能，先看第一页，点下一页，再按 Enter）")
        sys.stdin.readline()

        if not captured:
            print("\n❌ 没捕获到任何 JSON 请求。可能：")
            print("  1. 该功能纯前端渲染无后端 API")
            print("  2. 点击没触发网络请求")
            print("  3. 请求被过滤了（非 *.lzu.edu.cn / 静态资源）")
            print("\n建议标 cdp_only: true")
            browser.close()
            return

        # 打印捕获的请求
        print(f"\n{'='*60}")
        print(f"捕获到 {len(captured)} 个 JSON 请求:")
        print(f"{'='*60}")
        for i, item in enumerate(captured):
            print(f"\n[{i}] {item['method']} {item['url']}")
            print(f"    status: {item['status']}")
            print(f"    params: {json.dumps(item['params'], ensure_ascii=False)[:200]}")
            print(f"    headers: {json.dumps(item['headers'], ensure_ascii=False)[:200]}")
            print(f"    response: {item['response_preview']}")

        # 多选
        print(f"\n要记录哪些？（输入序号，逗号分隔，如 0,1,2；单个就输 0）")
        print(f"一次点击触发多个接口时，选多个会自动构成依赖链 steps 数组。")
        sel = input("> ").strip()
        try:
            indices = [int(x.strip()) for x in sel.split(",") if x.strip().isdigit()]
        except ValueError:
            indices = []
        if not indices:
            print("未选择，退出。")
            browser.close()
            return

        selected = [captured[i] for i in indices if 0 <= i < len(captured)]

        # 翻页 diff（可选）
        pagination_fields = []
        if len(selected) == 1 and selected[0]["method"] == "GET" and selected[0]["params"]:
            print(f"\n这个接口有分页吗？要自动发现页码字段吗？(y/N)")
            do_diff = input("> ").strip().lower().startswith("y")
            if do_diff:
                print("请在浏览器里点「下一页」，然后按 Enter...")
                before_count = len(captured)
                sys.stdin.readline()
                new_items = captured[before_count:]
                # 找同类请求
                target_url = selected[0]["url"].split("?")[0]
                matching = [n for n in new_items if n["url"].split("?")[0] == target_url]
                if matching:
                    changed = diff_params(selected[0]["params"], matching[-1]["params"])
                    if changed:
                        print(f"  ✅ 发现变化的参数: {changed}（可能是页码/偏移量）")
                        pagination_fields = changed
                    else:
                        print(f"  ❌ 两次请求参数相同，没找到页码字段")
                else:
                    print(f"  ❌ 没捕获到第二次同类请求")

        # 回放验证
        cookies = load_state_cookies()
        if not cookies:
            # 从 CDP context 导出 storage_state
            print("\n导出 storage_state（刷新 cookie）...")
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            ctx.storage_state(path=str(STATE_PATH))
            cookies = load_state_cookies()

        print(f"\n{'='*60}")
        print(f"回放验证（requests 原样打一遍，确认不是临时凭证）")
        print(f"{'='*60}")
        verified_steps = []
        all_ok = True
        for i, item in enumerate(selected):
            ok, msg = replay_verify(item, cookies)
            status = "✅" if ok else "❌"
            print(f"\n[{i}] {status} {item['method']} {item['url']}")
            print(f"    {msg}")
            if ok:
                verified_steps.append(item)
            else:
                all_ok = False

        if not all_ok:
            print(f"\n⚠️ 部分请求回放失败。可能依赖临时凭证，不适合走 HTTP。")
            print(f"仍要写入吗？(y/N)")
            if not input("> ").strip().lower().startswith("y"):
                print("取消写入。")
                browser.close()
                return

        # 构建 route 条目
        steps = []
        for i, item in enumerate(verified_steps if verified_steps else selected):
            params = dict(item["params"])
            # 翻页字段标占位符
            for pf in pagination_fields:
                if pf in params:
                    params[pf] = "${user_input:page}"

            step = {
                "endpoint": item["url"],
                "method": item["method"],
                "params": params,
                "headers_extra": {k: v for k, v in item["headers"].items()
                                  if k.lower() not in SENSITIVE_HEADERS and k.lower() not in (
                                      "host", "content-length", "user-agent", "accept",
                                      "accept-language", "accept-encoding", "connection", "origin",
                                      "sec-fetch-mode", "sec-fetch-site", "sec-fetch-dest",
                                      "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform",
                                      "referer", "content-type") and v},
                "auth": "portal_session",
                "response_path": "data",  # 默认整个 data，agent 后续可调
                "response_type": "dict",
            }
            # 多步链：前面的 step 加 save_as
            if len(verified_steps) > 1 and i < len(verified_steps) - 1:
                step["save_as"] = f"step_{i}_output"
            steps.append(step)

        route_entry = {
            "is_write": False,
            "steps": steps,
            "notes": f"侦察于 {date.today().isoformat()}",
            "discovered": date.today().isoformat(),
            "last_ok": date.today().isoformat() if all_ok else "",
            "fail_log": {"401": 0, "412": 0, "schema_mismatch": 0, "timeout": 0},
        }

        # 写入 routes.json
        routes = load_routes()
        routes[task_name] = route_entry
        save_routes(routes)
        print(f"\n✅ 已写入 {ROUTES_PATH}")
        print(f"   任务: {task_name}")
        print(f"   steps: {len(steps)} 步")
        print(f"   回放: {'全部通过' if all_ok else '部分失败(已确认写入)'}")
        if pagination_fields:
            print(f"   翻页字段: {pagination_fields} → ${{user_input:page}}")

        # 确保 storage_state 已导出
        if not STATE_PATH.exists():
            ctx.storage_state(path=str(STATE_PATH))
            print(f"   storage_state: 已导出到 {STATE_PATH}")

        browser.close()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="门户 API 侦察脚本")
    ap.add_argument("task", help="任务名（如 查校园卡余额）")
    ap.add_argument("--cdp", default="http://127.0.0.1:9222", help="CDP 地址")
    args = ap.parse_args()
    run_recon(args.task, args.cdp)
