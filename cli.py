#!/usr/bin/env python3
"""lzu-portal CLI 统一入口。

子命令:
    auth    导出 storage_state（cookie + localStorage）
    recon   侦察一个功能，抓 API 接口写入 routes.json
    query   查询（走 routes.json + requests，不开浏览器）
    routes  列出已侦察的路由
    status  检查 storage_state + routes 状态

用法:
    python3 skills/portal/cli.py auth
    python3 skills/portal/cli.py auth --cdp http://127.0.0.1:9222
    python3 skills/portal/cli.py recon "查校园卡余额"
    python3 skills/portal/cli.py query "查校园卡余额"
    python3 skills/portal/cli.py query "查消费记录" --param month=2026-07
    python3 skills/portal/cli.py routes
    python3 skills/portal/cli.py status
"""

import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from auth import export_via_playwright, export_via_cdp, check_status
from query import run_query, load_routes, RouteNotFoundError, QueryError
from recon import run_recon


def cmd_auth(args):
    if args.check:
        check_status()
    elif args.cdp:
        export_via_cdp(args.cdp)
    else:
        export_via_playwright()


def cmd_recon(args):
    run_recon(args.task, cdp_url=args.cdp or "http://127.0.0.1:9222")


def cmd_query(args):
    user_inputs = {}
    if args.param:
        for p in args.param:
            if "=" in p:
                k, v = p.split("=", 1)
                user_inputs[k.strip()] = v.strip()

    # 读取配置（优先当前目录，其次仓库根）
    cfg_path = Path("config.json")
    if not cfg_path.exists():
        parent_cfg = Path(__file__).resolve().parent.parent / "config.json"
        if parent_cfg.exists():
            cfg_path = parent_cfg

    # 特殊处理：校园卡余额（EasyTong 4 步流程）
    if args.task == "查校园卡余额":
        try:
            from easytong import get_campus_card_balance
            cfg = json.loads(cfg_path.read_text("utf-8"))
            username = cfg.get("lzu_username", "").split("@")[0]
            password = cfg.get("lzu_portal_password", "")
            result = get_campus_card_balance(username, password)
            if result.get("ok"):
                print(f"✅ 校园卡余额: {result['balance']} 元")
                print(f"   姓名: {result['name']}")
                print(f"   卡名: {result['card_name']}")
                print(f"   本月消费: {result['monthly_spend']} 元")
            else:
                print(f"❌ 查询失败: {result.get('error', '未知错误')}")
                sys.exit(1)
        except Exception as e:
            print(f"❌ 查询失败: {e}")
            sys.exit(1)
        return

    # 特殊处理：空教室查询（解析 skjc 字段，直接返回格式化结果）
    if args.task == "查空教室":
        try:
            from classroom import query_empty_classrooms
            cfg = json.loads(cfg_path.read_text("utf-8"))
            username = cfg.get("lzu_username", "").split("@")[0]
            password = cfg.get("lzu_portal_password", "")
            xqh = user_inputs.get("xqh", "02")
            building = user_inputs.get("building")
            floor = user_inputs.get("floor")
            if floor:
                floor = int(floor)
            date_str = user_inputs.get("date")
            slot = user_inputs.get("slot")
            result = query_empty_classrooms(username, password, xqh, building, floor, date_str, slot)
            if result.get("ok"):
                print(f"✅ 空教室查询: {result['date']} {result['slot']}")
                print(f"   总教室: {result['total']}，全空闲: {result['free']}")
                if result["free_rooms"]:
                    print(f"\n   全空闲教室:")
                    for room in result["free_rooms"][:20]:
                        print(f"     {room['name']} ({room['seats']}座, {room['floor']}层)")
                    if len(result["free_rooms"]) > 20:
                        print(f"     ... 共 {len(result['free_rooms'])} 间")
            else:
                print(f"❌ 查询失败: {result.get('error', '未知错误')}")
                sys.exit(1)
        except Exception as e:
            print(f"❌ 查询失败: {e}")
            sys.exit(1)
        return

    try:
        result = run_query(args.task, user_inputs=user_inputs)
    except RouteNotFoundError as e:
        print(f"❌ {e}")
        print(f"请先侦察: python3 skills/portal/cli.py recon \"{args.task}\"")
        sys.exit(1)
    except QueryError as e:
        print(f"❌ 查询失败: {e}")
        sys.exit(1)

    if result.get("ok"):
        print(f"✅ 查询成功: {result['task']}")
        data = result.get("data")
        if isinstance(data, (dict, list)):
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print(data)
    else:
        action = result.get("action", "")
        err = result.get("error", "未知错误")
        print(f"❌ 查询失败: {err}")
        if action == "reauth":
            print("→ cookie 过期，请重跑: python3 skills/portal/cli.py auth")
        elif action == "waf_refresh":
            print("→ WAF 拦截，需刷新 WAF token（CDP 刷一次 cookie）")
            print("  跑: python3 skills/portal/cli.py auth --cdp http://127.0.0.1:9222")
        elif action == "recon":
            print(f"→ 接口可能改版，请重新侦察: python3 skills/portal/cli.py recon \"{args.task}\"")
        elif action == "retry":
            print("→ 临时故障，稍后重试")
        sys.exit(1)


def cmd_routes(args):
    routes = load_routes()
    if not routes:
        print("routes.json 为空，还没有侦察过任何功能。")
        print("开始侦察: python3 skills/portal/cli.py recon \"查校园卡余额\"")
        return
    print(f"已侦察 {len(routes)} 个路由:\n")
    for name, route in routes.items():
        steps = route.get("steps", [])
        is_write = route.get("is_write", False)
        last_ok = route.get("last_ok", "")
        fail_log = route.get("fail_log", {})
        total_fails = sum(fail_log.values())
        tag = "📝写" if is_write else "📖读"
        status = "✅" if last_ok and total_fails == 0 else "⚠️"
        print(f"  {status} {tag} {name}")
        print(f"     steps: {len(steps)} | last_ok: {last_ok or '—'} | fails: {total_fails}")
        if total_fails:
            for k, v in fail_log.items():
                if v:
                    print(f"     {k}: {v}")


def cmd_status(args):
    print("=== storage_state ===")
    check_status()
    print("\n=== routes.json ===")
    cmd_routes(args)


def main():
    import argparse
    ap = argparse.ArgumentParser(
        description="兰大个人工作台 portal skill",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd")

    # auth
    p_auth = sub.add_parser("auth", help="导出 storage_state")
    p_auth.add_argument("--cdp", help="从已开 CDP 的 Chrome 导出")
    p_auth.add_argument("--check", action="store_true", help="只检查不导出")
    p_auth.set_defaults(func=cmd_auth)

    # recon
    p_recon = sub.add_parser("recon", help="侦察 API 接口")
    p_recon.add_argument("task", help="任务名（如 查校园卡余额）")
    p_recon.add_argument("--cdp", default="http://127.0.0.1:9222", help="CDP 地址")
    p_recon.set_defaults(func=cmd_recon)

    # query
    p_query = sub.add_parser("query", help="查询（走 HTTP）")
    p_query.add_argument("task", help="任务名")
    p_query.add_argument("--param", action="append", help="用户输入参数 key=value（可多次）")
    p_query.set_defaults(func=cmd_query)

    # routes
    p_routes = sub.add_parser("routes", help="列出已侦察路由")
    p_routes.set_defaults(func=cmd_routes)

    # status
    p_status = sub.add_parser("status", help="检查整体状态")
    p_status.set_defaults(func=cmd_status)

    args = ap.parse_args()
    if not args.cmd:
        ap.print_help()
        return 0
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main() or 0)
