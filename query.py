#!/usr/bin/env python3
"""查询引擎：读 routes.json + storage_state，用 requests 打 API，解析返回。

用法:
    from skills.portal.query import run_query
    result = run_query("查校园卡余额")

    # 带用户输入参数
    result = run_query("查消费记录", user_inputs={"month": "2026-07"})

执行路径:
    1. 读 routes.json 找任务路由
    2. 读 storage_state 获取 cookie
    3. 按 steps 顺序执行，解析动态参数占位符
    4. 失败按 fail_log 分类处理
"""

import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

import requests

ROUTES_PATH = Path.home() / ".openclaw-lzu" / "portal_routes.json"
STATE_PATH = Path.home() / ".openclaw-lzu" / "portal_state.json"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")

FAIL_THRESHOLDS = {"schema_mismatch": 2, "timeout": 2}


class QueryError(Exception):
    pass


class CookieExpiredError(QueryError):
    pass


class WAFBlockedError(QueryError):
    pass


class RouteStaleError(QueryError):
    pass


class RouteNotFoundError(QueryError):
    pass


def load_routes() -> dict:
    if not ROUTES_PATH.exists():
        return {}
    return json.loads(ROUTES_PATH.read_text("utf-8"))


def save_routes(routes: dict):
    ROUTES_PATH.parent.mkdir(parents=True, exist_ok=True)
    ROUTES_PATH.write_text(json.dumps(routes, ensure_ascii=False, indent=2), "utf-8")


def load_cookies() -> dict:
    if not STATE_PATH.exists():
        raise CookieExpiredError(
            f"storage_state 不存在: {STATE_PATH}\n"
            "请先跑: python3 skills/portal/cli.py auth"
        )
    state = json.loads(STATE_PATH.read_text("utf-8"))
    cookies = {c["name"]: c["value"]
               for c in state.get("cookies", [])
               if "lzu.edu.cn" in c.get("domain", "")}
    if not cookies:
        raise CookieExpiredError("storage_state 里没有 lzu.edu.cn cookie，请重新导出")
    return cookies


def resolve_placeholder(value, user_inputs: dict, step_vars: dict):
    """解析动态参数占位符 ${user_input:xxx} / ${compute:xxx} / ${step:xxx} / ${xxx}。"""
    if not isinstance(value, str):
        return value
    # 先试 ${source:name} 格式
    m = re.match(r"^\$\{(\w+):(\w+)\}$", value)
    if m:
        source, name = m.group(1), m.group(2)
        if source == "user_input":
            if name not in user_inputs:
                raise QueryError(f"缺少用户输入参数: {name}")
            return user_inputs[name]
        if source == "step":
            if name not in step_vars:
                raise QueryError(f"缺少前序 step 输出: {name}")
            return step_vars[name]
        if source == "compute":
            return compute_value(name)
        return value
    # 再试 ${name} 格式（从 step_vars 取）
    m2 = re.match(r"^\$\{(\w+)\}$", value)
    if m2:
        name = m2.group(1)
        if name in step_vars:
            return step_vars[name]
        if name in user_inputs:
            return user_inputs[name]
    return value


def compute_value(name: str) -> str:
    """运行时计算的动态值。"""
    today = date.today()
    if name == "today":
        return today.isoformat()
    if name == "current_term":
        # LZU 学期代码格式: YYYY+学期(1=春/2=秋)，如 20261=2026春
        year = today.year
        if today.month >= 8:
            return f"{year}2"
        else:
            return f"{year}1"
    if name == "current_year":
        return str(today.year)
    raise QueryError(f"未知的 compute 类型: {name}")


def resolve_params(params: dict, user_inputs: dict, step_vars: dict) -> dict:
    """递归解析 params 里的所有占位符。"""
    resolved = {}
    for k, v in params.items():
        if isinstance(v, dict):
            resolved[k] = resolve_params(v, user_inputs, step_vars)
        elif isinstance(v, str):
            resolved[k] = resolve_placeholder(v, user_inputs, step_vars)
        else:
            resolved[k] = v
    return resolved


def extract_value(data, path: str):
    """按 jq-like 路径取值: data.balance / data.list / data.records.0.name"""
    if not path:
        return data
    parts = path.split(".")
    cur = data
    for part in parts:
        if isinstance(cur, dict):
            if part in cur:
                cur = cur[part]
            elif part.isdigit() and part in cur:
                cur = cur[part]
            else:
                raise QueryError(f"路径 {path} 字段 {part} 不存在")
        elif isinstance(cur, list) and part.isdigit():
            idx = int(part)
            if idx < len(cur):
                cur = cur[idx]
            else:
                raise QueryError(f"路径 {path} 索引 {idx} 越界")
        else:
            raise QueryError(f"路径 {path} 在 {type(cur)} 上无法继续")
    return cur


def execute_step(step: dict, cookies: dict, user_inputs: dict, step_vars: dict):
    """执行单个 step，返回 (raw_response, extracted_value)。"""
    endpoint = step["endpoint"]
    method = step.get("method", "GET").upper()
    params = resolve_params(step.get("params", {}), user_inputs, step_vars)
    headers = {"User-Agent": UA}
    # 解析 headers_extra 中的占位符
    headers_extra = resolve_params(step.get("headers_extra", {}), user_inputs, step_vars)
    headers.update(headers_extra)

    try:
        if method == "GET":
            r = requests.get(endpoint, params=params, headers=headers,
                             cookies=cookies, timeout=15, allow_redirects=False)
        elif method == "POST":
            if params and headers.get("Content-Type", "").startswith("application/json"):
                r = requests.post(endpoint, json=params, headers=headers,
                                  cookies=cookies, timeout=15, allow_redirects=False)
            else:
                r = requests.post(endpoint, data=params, headers=headers,
                                  cookies=cookies, timeout=15, allow_redirects=False)
        else:
            raise QueryError(f"不支持的 method: {method}")
    except requests.Timeout:
        raise QueryError("timeout")
    except requests.ConnectionError as e:
        raise QueryError(f"网络错误: {e}")

    # 失败分类
    if r.status_code in (401,) or (r.status_code == 302 and "sso" in r.headers.get("Location", "")):
        raise CookieExpiredError(f"HTTP {r.status_code} cookie 过期")
    if r.status_code == 412:
        raise WAFBlockedError("HTTP 412 WAF 拦截")
    if r.status_code != 200:
        raise QueryError(f"HTTP {r.status_code}: {r.text[:200]}")

    try:
        body = r.json()
    except Exception:
        raise QueryError("schema_mismatch: 响应非 JSON")

    response_path = step.get("response_path", "data")
    try:
        value = extract_value(body, response_path)
    except QueryError:
        raise QueryError("schema_mismatch: response_path 取不到值")

    return body, value


def classify_error(e: Exception) -> str:
    """把异常分类到 fail_log key。"""
    if isinstance(e, CookieExpiredError):
        return "401"
    if isinstance(e, WAFBlockedError):
        return "412"
    msg = str(e)
    if "schema_mismatch" in msg:
        return "schema_mismatch"
    if "timeout" in msg or "网络错误" in msg:
        return "timeout"
    return "schema_mismatch"


def run_query(task_name: str, user_inputs: dict | None = None) -> dict:
    """查询主入口。返回 {"ok": bool, "data": ..., "error": ...}。

    失败时返回 ok=False + error 分类，调用方决定是否触发侦察/重导 cookie。
    """
    user_inputs = user_inputs or {}
    routes = load_routes()

    # 自动从 config.json 读取凭据（供 ${username} / ${password} 占位符使用）
    # 优先从当前目录读，其次从仓库根目录读
    config_path = Path("config.json")
    if not config_path.exists():
        # 尝试从仓库根目录读（portal-skill 在仓库根下的子目录）
        parent_config = Path(__file__).resolve().parent.parent / "config.json"
        if parent_config.exists():
            config_path = parent_config
    if config_path.exists():
        cfg = json.loads(config_path.read_text("utf-8"))
        if "username" not in user_inputs:
            raw_user = cfg.get("lzu_username", "")
            user_inputs["username"] = raw_user.split("@")[0] if raw_user else ""
        if "password" not in user_inputs:
            user_inputs["password"] = cfg.get("lzu_portal_password", "")

    if task_name not in routes:
        raise RouteNotFoundError(
            f"routes.json 没有任务「{task_name}」\n"
            f"请先侦察: python3 skills/portal/cli.py recon \"{task_name}\""
        )

    route = routes[task_name]
    steps = route.get("steps", [])
    if not steps:
        raise RouteStaleError(f"任务「{task_name}」没有 steps")

    # 写操作确认
    if route.get("is_write") and route.get("require_confirm"):
        print(f"⚠️ 写操作确认: {task_name}")
        print(f"   endpoint: {steps[-1].get('endpoint', '?')}")
        print(f"   params: {json.dumps(steps[-1].get('params', {}), ensure_ascii=False)}")
        print(f"确认执行？(输入 yes)")
        confirm = input("> ").strip()
        if confirm != "yes":
            return {"ok": False, "error": "用户取消写操作"}

    try:
        cookies = load_cookies()
    except CookieExpiredError as e:
        return {"ok": False, "error": str(e), "action": "reauth"}

    step_vars = {}
    results = []

    for i, step in enumerate(steps):
        try:
            body, value = execute_step(step, cookies, user_inputs, step_vars)
            results.append({"step": i, "value": value, "body": body})
            # save_as 供后续 step 用
            save_as = step.get("save_as")
            if save_as:
                step_vars[save_as] = value
        except (CookieExpiredError, WAFBlockedError) as e:
            # 这些不需要重试，直接返回
            fail_key = classify_error(e)
            route["fail_log"][fail_key] = route["fail_log"].get(fail_key, 0) + 1
            route["last_ok"] = ""
            routes[task_name] = route
            save_routes(routes)
            action = "reauth" if fail_key == "401" else ("waf_refresh" if fail_key == "412" else "recon")
            return {"ok": False, "error": str(e), "action": action, "fail_key": fail_key}
        except QueryError as e:
            fail_key = classify_error(e)
            route["fail_log"][fail_key] = route["fail_log"].get(fail_key, 0) + 1
            threshold = FAIL_THRESHOLDS.get(fail_key, 2)
            if route["fail_log"][fail_key] >= threshold:
                route["last_ok"] = ""
                routes[task_name] = route
                save_routes(routes)
                return {"ok": False, "error": str(e), "action": "recon", "fail_key": fail_key}
            # 未达阈值，继续返回错误但标 action=retry
            routes[task_name] = route
            save_routes(routes)
            return {"ok": False, "error": str(e), "action": "retry", "fail_key": fail_key}

    # 成功：更新 last_ok，清 fail_log
    route["last_ok"] = date.today().isoformat()
    for k in route.get("fail_log", {}):
        route["fail_log"][k] = 0
    routes[task_name] = route
    save_routes(routes)

    # 返回最后一步的 value（通常是最终数据）
    final_value = results[-1]["value"] if results else None
    all_bodies = [r["body"] for r in results]
    return {"ok": True, "data": final_value, "bodies": all_bodies, "task": task_name}
