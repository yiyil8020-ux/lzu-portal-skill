#!/usr/bin/env python3
"""空教室查询：解析 skjc 字段，直接返回格式化的空闲教室。"""
import json
import requests
from datetime import datetime
from pathlib import Path

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/149.0.0.0 Safari/537.36"

# 节次定义：14位 skjc 字段的索引 → 节次名称
SLOT_LABELS = ['1', '2', '3', '4', '中', '中2', '5', '6', '7', '8', '9', '10', '11', '12']

# 时段分组
TIME_SLOTS = {
    '上午': [0, 1, 2, 3],      # 1-4节
    '下午': [6, 7, 8, 9],      # 5-8节
    '晚上': [10, 11, 12, 13],  # 9-12节
    '全天': list(range(14)),
}


def query_empty_classrooms(username: str, password: str, xqh: str, jxlh: str,
                           date_str: str = None, slot: str = None) -> dict:
    """查询空教室。

    Args:
        username: 学号
        password: 密码
        xqh: 校区号（如 '02' = 榆中）
        jxlh: 教学楼号（如 '02010017' = 天山堂）
        date_str: 查询日期 YYYY-MM-DD（默认今天）
        slot: 时段筛选（'上午'/'下午'/'晚上'/'全天'，默认全天）

    Returns:
        {"ok": bool, "rooms": [...], "date": str, "slot": str, "total": int, "free": int}
    """
    try:
        if not date_str:
            date_str = datetime.now().strftime("%Y-%m-%d")

        # Step 1: 登录
        r1 = requests.post(
            "https://appservice.lzu.edu.cn/api/eusp-unify-terminal/app-user/login",
            headers={"User-Agent": UA, "Content-Type": "application/json"},
            json={"app_os": 2, "name": username, "pwd": password},
            timeout=15,
        )
        if r1.json().get("code") != 1:
            return {"ok": False, "error": f"登录失败: {r1.json().get('message', '')}"}
        gateway_token = r1.json()["data"]["gateway_token"]

        # Step 2: 查询空教室
        r2 = requests.post(
            "https://appservice.lzu.edu.cn/api/lzu-teaching-research/V2/kjscx/getJsxx",
            headers={"User-Agent": UA, "Authorization": gateway_token, "Content-Type": "application/json"},
            json={"xqh": xqh, "jxlh": jxlh, "cur_page": 1, "record_per_page": 200, "rq": date_str},
            timeout=15,
        )
        if r2.json().get("code") != 1:
            return {"ok": False, "error": f"查询失败: {r2.json().get('message', '')}"}

        rooms = r2.json()["data"]["dto_list"]

        # Step 3: 解析 skjc 字段（1=空闲，0=占用）
        if slot and slot in TIME_SLOTS:
            check_indices = TIME_SLOTS[slot]
        else:
            check_indices = TIME_SLOTS['全天']

        result = []
        for room in rooms:
            skjc = room["skjc"].split(",")
            free_slots = [SLOT_LABELS[i] for i in check_indices if i < len(skjc) and skjc[i] == "1"]
            occupied_slots = [SLOT_LABELS[i] for i in check_indices if i < len(skjc) and skjc[i] == "0"]
            is_free = len(free_slots) == len(check_indices)

            result.append({
                "name": room["jsmc"],
                "seats": room["zws"],
                "floor": room["szlc"],
                "is_free": is_free,
                "free_slots": free_slots,
                "occupied_slots": occupied_slots,
                "free_count": len(free_slots),
                "skjc_raw": room["skjc"],
            })

        # 按空闲程度排序（全空闲在前）
        result.sort(key=lambda x: (-x["free_count"], x["name"]))

        free_rooms = [r for r in result if r["is_free"]]

        return {
            "ok": True,
            "date": date_str,
            "slot": slot or "全天",
            "total": len(result),
            "free": len(free_rooms),
            "rooms": result,
            "free_rooms": free_rooms,
        }

    except Exception as e:
        return {"ok": False, "error": str(e)}
