#!/usr/bin/env python3
"""storage_state 导出：Playwright 打开 my.lzu.edu.cn，用户手动登录，导出 cookie+localStorage。

用法:
    python3 skills/portal/auth.py                    # 弹浏览器，手动登录，导出
    python3 skills/portal/auth.py --cdp http://127.0.0.1:9222  # 从已开 CDP 的 Chrome 导出
"""

import json
import os
from pathlib import Path

STATE_PATH = Path.home() / ".openclaw-lzu" / "portal_state.json"
PORTAL_HOME = "https://my.lzu.edu.cn/mylzu/home"


def export_via_playwright():
    """启动 Playwright 浏览器，用户手动登录，导出 storage_state。"""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(
            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 Chrome/149.0.0.0 Safari/537.36"),
        )
        page = ctx.new_page()
        page.goto("https://my.lzu.edu.cn")

        print("浏览器已打开 my.lzu.edu.cn")
        print("请在浏览器里完成登录（SSO + 验证码 + 人脸核验）。")
        print("看到「兰州大学个人工作台」首页后，回终端按 Enter 导出...")
        input()

        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        ctx.storage_state(path=str(STATE_PATH))

        state = json.loads(STATE_PATH.read_text("utf-8"))
        cookie_count = len(state.get("cookies", []))
        lzu_cookies = [c for c in state.get("cookies", []) if "lzu.edu.cn" in c.get("domain", "")]
        ls_count = sum(len(o.get("localStorage", [])) for o in state.get("origins", []))

        print(f"\n✅ storage_state 已导出: {STATE_PATH}")
        print(f"   cookies: {cookie_count} 个（其中 lzu.edu.cn: {len(lzu_cookies)}）")
        print(f"   localStorage: {ls_count} 条")
        print(f"   关键 cookie:")
        for c in lzu_cookies:
            name = c["name"]
            val = c["value"]
            masked = f"****{val[-4:]}" if len(val) > 8 else "****"
            print(f"     {name} ({c.get('domain', '?')}): {masked}")

        browser.close()


def export_via_cdp(cdp_url: str = "http://127.0.0.1:9222"):
    """从已开 CDP 的 Chrome 导出 storage_state（不需要重新登录）。"""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cdp_url)
        ctx = browser.contexts[0]

        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        ctx.storage_state(path=str(STATE_PATH))

        state = json.loads(STATE_PATH.read_text("utf-8"))
        lzu_cookies = [c for c in state.get("cookies", []) if "lzu.edu.cn" in c.get("domain", "")]

        print(f"✅ storage_state 已导出: {STATE_PATH}")
        print(f"   lzu.edu.cn cookies: {len(lzu_cookies)} 个")
        for c in lzu_cookies:
            name = c["name"]
            val = c["value"]
            masked = f"****{val[-4:]}" if len(val) > 8 else "****"
            print(f"     {name}: {masked}")

        browser.close()


def check_status() -> bool:
    """检查 storage_state 是否存在且有效（有 lzu.edu.cn cookie）。"""
    if not STATE_PATH.exists():
        print(f"❌ storage_state 不存在: {STATE_PATH}")
        print("请跑: python3 skills/portal/cli.py auth")
        return False
    state = json.loads(STATE_PATH.read_text("utf-8"))
    lzu_cookies = [c for c in state.get("cookies", []) if "lzu.edu.cn" in c.get("domain", "")]
    if not lzu_cookies:
        print(f"❌ storage_state 里没有 lzu.edu.cn cookie")
        print("请重新跑: python3 skills/portal/cli.py auth")
        return False
    print(f"✅ storage_state 有效: {len(lzu_cookies)} 个 lzu.edu.cn cookie")
    print(f"   路径: {STATE_PATH}")
    has_sso = any(c["name"] in ("iPlanetDirectoryPro", "CASTGC") for c in lzu_cookies)
    print(f"   SSO cookie: {'有' if has_sso else '❌ 缺'}")
    return True


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="门户 storage_state 导出")
    ap.add_argument("--cdp", help="从已开 CDP 的 Chrome 导出（不弹新浏览器）")
    ap.add_argument("--check", action="store_true", help="只检查状态不导出")
    args = ap.parse_args()

    if args.check:
        check_status()
    elif args.cdp:
        export_via_cdp(args.cdp)
    else:
        export_via_playwright()
