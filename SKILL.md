---
name: lzu-portal
version: 0.3.1
license: MIT
description: |
  兰大校园助手 —— 查成绩、查课表、查校园卡、查空教室、查考试安排，一句话搞定。
  首次用某个功能会自动"侦察"一下接口，之后就直接调接口，不用再开浏览器。
  适用于任何能跑 shell 的 AI 助手（OpenClaw / OpenCode / Claude Code / Codex / Cursor / Gemini CLI 等）。
触发场景: 兰大个人工作台上的任何操作 —— 查校园卡余额/消费、查考试安排、查成绩、查课表、网费/电费充值、查服务时间、查空教室、通讯录、场馆预约、本科质量监测、教务系统操作、...
触发词: 校园卡, 一卡通, 余额, 考试安排, 成绩, 课表, 网费, 电费, 服务时间, 空教室, 通讯录, 场馆预约, 评教, 本科质量监测, 教务系统, 个人工作台, 门户, my.lzu, portal
---

# 兰大个人工作台 (lzu-portal) — 侦察/查询分离

> **核心思路**：浏览器只用来"侦察"（每个功能一次，抓背后 API），日常查询走纯 HTTP。
> 侦察记录存 `~/.openclaw-lzu/portal_routes.json`，查询时直接 requests 打接口。

## 执行路径（agent 每次按顺序判断）

```
1. 查 routes.json → 有该任务的路由？
   ├─ 有 → requests 打 API（快，不开浏览器）
   │       ├─ 成功 → 返回数据，更新 last_ok
   │       └─ 失败 → 按 fail_log 分类处理（见下表），不笼统计数
   └─ 无 → 走 2

   失败分类处理：
   ┌──────────────────┬─────────────────────────────────────────┐
   │ fail_log key     │ 动作                                    │
   ├──────────────────┼─────────────────────────────────────────┤
   │ 401 / 302→sso    │ cookie 过期 → 提示用户重导 storage_state     │
   │                  │ （不是接口坏了，不触发重新侦察）            │
   │ 412              │ WAF 拦截 → 走 1.5 刷新 WAF token           │
   │ schema_mismatch  │ 接口改版 → 计数≥2 触发走 2 重新侦察         │
   │ timeout          │ 网络抖动 → 重试 1 次，再失败走 2            │
   └──────────────────┴─────────────────────────────────────────┘

1.5 WAF token 周期刷新（412 专用）
   WAF 的放行凭证（cookie/token）通常有 TTL（几十分钟到几小时）。
   412 时 → CDP 刷一次 WAF cookie 写回 storage_state → 后续查询继续走 HTTP
   性质类似 zanao 的 token 刷新，不是"每次查询都开浏览器"

2. 路由缺失或 schema_mismatch 超阈值 → 侦察
   ├─ 告诉用户："首次使用 [任务名]，需要侦察一次。"
   ├─ 启动侦察脚本（skills/portal/recon.py）
   │   CDP 连 Chrome → 开 network 监听 → 用户手动点目标功能
   │   → 脚本自动抓 request URL/method/params/headers + response JSON
   │   → 落盘前自动用 requests 回放验证（确认不是临时凭证）
   │   → 验证通过写入 routes.json + 导出 storage_state
   ├─ 侦察完 → 回到 1，用 HTTP 打接口
   └─ 侦察发现接口需要动态 token → 走 3

3. 动态 token：先试 HTML 抠取（不上 CDP）
   很多"动态 token"（CSRF token 等）嵌在普通 HTML 页面的 <meta> / <input hidden> 里。
   → requests GET 那个 HTML 页面 → 正则/BS4 抠 token → 拿去打 API
   → 只有 token 是 JS 运行时算的（混淆 sign/滑块）才走 4

4. CDP 兜底（最后手段）
   只有以下情况走 CDP（记录 "cdp_only": true）：
   - token 是 JS 运行时计算，HTML 抠不到
   - 功能纯前端计算无后端 API
   - 写操作需要复杂交互（多步表单 + 验证码）
```

## 写操作确认

routes.json 里 `is_write: true` 的任务（场馆预约/提交申请等），agent 执行前**必须**向用户复述操作内容并取得确认（跟 zanao 的 `--yes` 一个道理）。`require_confirm: true` 时强制确认，不确认不执行。

## cookie 获取：Playwright storage_state

不用手动复制 Cookies 文件。用 Playwright 的 `storage_state` 导出完整浏览器状态（cookies + localStorage）：

```python
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto("https://my.lzu.edu.cn")
    # 用户手动登录（SSO + 验证码 + 人脸）...
    page.wait_for_url("**/mylzu/home", timeout=120000)
    ctx.storage_state(path="~/.openclaw-lzu/portal_state.json")
    browser.close()
```

查询阶段读 cookie：
```python
state = json.load(open("~/.openclaw-lzu/portal_state.json"))
cookies = {c["name"]: c["value"] for c in state["cookies"] if "lzu.edu.cn" in c.get("domain","")}
```

cookie 过期标志：401/302 → sso → 提示重导 storage_state。

## 门户 DOM 结构（侦察 + CDP 兜底参考）

- 首页 `https://my.lzu.edu.cn/mylzu/home`，title `兰州大学个人工作台`
- 首页内容在**父页 DOM**里（不是 iframe），Element UI（`.el-carousel` 轮播收藏入口）
- 2 个 iframe：`lzu_grgzt_fx/#/index`（主应用）、`lzu_xzgw_xzgw/#/xzgw`（行政公务）
- **我的收藏**（13 个常用入口）：校园卡/网费充值/邮箱/单位通讯录/服务时间/成绩查询/移动图书馆/空教室查询/宿舍电费/课程表/考试安排/正版化平台/应用市场/服务大厅
- **快速导航**（22 个直达入口）：本科质量监测/教务系统/智慧一卡通/就业系统/场馆预约/大型仪器预约/...
- WAF：首页走 WAF（412 + JS challenge），API 端点通常不走（侦察验证）。

## 跟现有 skill 的分工

- **门户内的操作**（校园卡/成绩/考试安排/网费/...） → 本 skill（HTTP 优先，侦察兜底）
- **有独立 API/协议的** → 现有 skill 更快：邮件 `lzu_mail`（IMAP）/ 赞噢 `zanao`（API）/ 学习通 `lzu_chaoxing`（自有 cookie）/ 课表全表 `lzu_timetable`（jwk 无 WAF）/ 新闻聚合 `lzu_news`

> 📦 侦察脚本契约 + routes.json 完整字段 + WAF 注意见 `skills/portal/AGENTS.md`。

## 已验证的 API 端点

| 任务 | 端点 | 认证方式 | 方法 | 参数 |
|---|---|---|---|---|
| 查考试安排 | `appservice.lzu.edu.cn/api/lzu-teaching-research/ksap/bksksap` | gateway_token | GET | 无（返回当前学期） |
| 查课表 | `appservice.lzu.edu.cn/api/lzu-teaching-research/kcb/getZdyCourse` | gateway_token | GET | zc=周次, qsbz=0 |
| 查学期信息 | `appservice.lzu.edu.cn/api/lzu-teaching-research/kcb/getXlxx` | gateway_token | POST | 无 |
| 查用户信息 | `my.lzu.edu.cn/api/eusp-unify-terminal/individual-workspace/userInfo` | gateway_token | GET | 无 |
| 查校园卡余额 | `my.lzu.edu.cn/.../etongGetWalletMoney` | **EasyTong ET token** | GET | 无 |
| 查未读消息 | `my.lzu.edu.cn/api/eusp-terminal-message/message-collect/messageStatus` | gateway_token | POST | 无 |
| 查最新通知 | `my.lzu.edu.cn/api/eusp-news-notice/news/address/getZjNews` | gateway_token | POST | current/size |
| 查空教室 | `appservice.lzu.edu.cn/api/lzu-teaching-research/V2/kjscx/getJsxx` | gateway_token | POST | xqh/jxlh/cur_page/rq |

## 认证方式

| 认证方式 | 用途 | 获取方式 | 有效期 |
|---|---|---|---|
| SSO cookie | 门户聚合 API（消息/通知/校园卡等） | 浏览器登录 → storage_state | 几小时到几天 |
| loginToken | 服务目录 / ST 获取 | POST `/app-user/login` | 长期 |
| **gateway_token** | **数据接口**（空教室/成绩等） | 登录接口返回 | **至少 5 分钟，可缓存复用** |
| ST ticket | 免密打开服务网页 | GET `/app-user/getSt` | 一次性 |

**gateway_token 使用**：登录后缓存，后续请求 `Authorization: {gateway_token}`，过期时重新登录。不需要每次重新登录。

## 特殊字段说明

### 空教室 skjc 字段

空教室查询返回的 `skjc` 字段是 14 位二进制字符串，对应节次：
- 位置 0-13 对应：`1, 2, 3, 4, 中, 中2, 5, 6, 7, 8, 9, 10, 11, 12`
- **`1` = 空闲，`0` = 占用**（注意：不是 0=空闲）

**教室名称匹配**：用户可能会用简称（如「A501」），实际 API 返回的是全称（如「医学校区A区501」或「天山堂A501」）。匹配时应灵活处理：优先精确匹配，匹配不到就用包含匹配，并结合用户上下文（校区/教学楼）判断是哪间教室。

### 校车发车时间

查询校车时间表时提醒用户：**发车表列出的是固定发车时间，其余时间校车坐满即发**（等待时间约 15 分钟）。如果用户问某个时间点有没有校车，先查发车表，如果表里没有，再告诉用户可以去站点等坐满即发的车。

### EasyTong 校园卡密钥

EasyTong 校园卡查询需要 MD5 签名。密钥从 FasterLZU app.js 逆向获得：
- `MD5KeyYm = ok15we1@oid8x5afd@`
- 签名方式：`MD5(AccNum|Time|MD5KeyYm)` 或 `MD5(AccNum|EPID|Time|MD5KeyYm)`
