#!/usr/bin/env python3
"""EasyTong 校园卡查询：4 步流程（登录→ST→ET token→EPID→余额）。"""
import hashlib
import json
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
import requests

MD5_KEY = "ok15we1@oid8x5afd@"
EASYTONG_BASE = "http://app.lzu.edu.cn:8080"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/149.0.0.0 Safari/537.36"


def md5_sign(*args):
    return hashlib.md5("|".join(str(a) for a in args).encode()).hexdigest()


def get_campus_card_balance(username: str, password: str) -> dict:
    """查询校园卡余额。返回 {"ok": bool, "balance": str, "card_name": str, ...}"""
    try:
        # Step 1: 登录拿 loginToken
        r1 = requests.post(
            "https://appservice.lzu.edu.cn/api/eusp-unify-terminal/app-user/login",
            headers={"User-Agent": UA, "Content-Type": "application/json"},
            json={"app_os": 2, "name": username, "pwd": password},
            timeout=15,
        )
        if r1.json().get("code") != 1:
            return {"ok": False, "error": f"登录失败: {r1.json().get('message','')}"}
        login_token = r1.json()["data"]["login_token"]

        # Step 2: 拿 ST（校园卡 serviceId = 29597）
        r2 = requests.get(
            "https://my.lzu.edu.cn/api/eusp-unify-terminal/app-user/getSt",
            headers={"User-Agent": UA},
            params={"loginToken": login_token, "serviceId": "29597", "service": ""},
            timeout=10,
        )
        if r2.json().get("code") != 1:
            return {"ok": False, "error": f"获取 ST 失败: {r2.json().get('message','')}"}
        st = r2.json()["data"]

        # Step 3: 换 ET token
        time_str = datetime.now().strftime("%Y%m%d%H%M%S")
        r3 = requests.post(
            f"{EASYTONG_BASE}/easytong_app/ExchangeEtToken",
            headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded"},
            data=f"Time={time_str}&St={st}&ContentType=application%2Fjson",
            timeout=10,
        )
        if r3.json().get("code") != 1:
            return {"ok": False, "error": f"获取 ET token 失败: {r3.json().get('msg','')}"}
        et_token = r3.json()["token"]
        acc_num = r3.json()["accNum"]

        # Step 4: 拿 EPID
        time_str2 = datetime.now().strftime("%Y%m%d%H%M%S")
        sign = md5_sign(acc_num, time_str2, MD5_KEY)
        r4 = requests.post(
            f"{EASYTONG_BASE}/easytong_app/GetAccInfo",
            headers={"User-Agent": UA, "Authorization": et_token,
                     "Content-Type": "application/x-www-form-urlencoded"},
            data=f"AccNum={acc_num}&Time={time_str2}&Sign={sign}&ContentType=application%2Fjson",
            timeout=10,
        )
        root = ET.fromstring(r4.text)
        if root.find(".//Code").text != "1":
            return {"ok": False, "error": f"获取账户信息失败: {root.find('.//Msg').text}"}
        epid = root.find(".//EPID").text
        acc_name = root.find(".//AccName").text

        # Step 5: 查余额
        time_str3 = datetime.now().strftime("%Y%m%d%H%M%S")
        sign2 = md5_sign(acc_num, epid, time_str3, MD5_KEY)
        r5 = requests.post(
            f"{EASYTONG_BASE}/easytong_app/GetWalletMoney",
            headers={"User-Agent": UA, "Authorization": et_token,
                     "Content-Type": "application/x-www-form-urlencoded"},
            data=f"AccNum={acc_num}&EPID={epid}&Time={time_str3}&Sign={sign2}&ContentType=application%2Fjson",
            timeout=10,
        )
        root2 = ET.fromstring(r5.text)
        if root2.find(".//Code").text != "1":
            return {"ok": False, "error": f"查询余额失败: {root2.find('.//Msg').text}"}

        return {
            "ok": True,
            "name": acc_name,
            "balance": root2.find(".//WalletMoney").text,
            "card_name": root2.find(".//CardName").text,
            "monthly_spend": root2.find(".//MonCard").text,
            "acc_num": acc_num,
            "epid": epid,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}
