# AGENTS.md — lzu-portal skill

## 架构：登录/取数据分离 + 侦察/查询分离

```
┌─────────────────────────────────────────────────────────────┐
│ 第一层：登录（一次性，批量解决）                               │
│                                                             │
│   loginToken = POST /app-user/login {name, pwd}             │
│        ↓                                                    │
│   服务目录 = POST /getServiceInfoDetailByTerminalRole        │
│        ↓  (AES 加密 body: terminalId=1&loginToken=xxx)      │
│   全部服务的 h5_url + service_info_id                        │
│        ↓                                                    │
│   ST = GET /app-user/getSt?loginToken=&serviceId=           │
│        ↓                                                    │
│   免密打开: h5_url?PersonID=xxx&st=xxx&ticket=xxx           │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│ 第二层：取数据（每个服务单独侦察）                             │
│                                                             │
│   已逆向的服务 → 直接拿 JSON（课表/校园卡/考试/消息/通知...）    │
│   未逆向的服务 → 先免密打开网页，再侦察背后 API                 │
│   纯前端服务   → CDP 兜底（标记 cdp_only）                    │
└─────────────────────────────────────────────────────────────┘
```

### 执行路径 4 级

1. **HTTP 直打**：routes.json 有路由 + cookie/loginToken/gateway_token 有效 → requests 直接拿 JSON
2. **WAF 刷新**：412 时 CDP 刷一次 WAF cookie → 继续 HTTP
3. **侦察**：路由缺失 → CDP 监听用户操作，抓 API，回放验证，落盘
4. **CDP 兜底**：纯前端无 API / JS 计算 token → 标 `cdp_only`，每次开浏览器

## WAF：页面挡，API 大概率不挡

`my.lzu.edu.cn` 有 anti-bot WAF（412 + `$_ts` 混淆 JS）。实测：
- requests 直连**首页** `my.lzu.edu.cn/mylzu/home` → 412
- requests 打 **API 端点**（如 `/lzu_grgzt_fx/api/...`）→ **大概率放行**（WAF 通常只挡页面入口，API 端点靠 cookie 鉴权不查 JS challenge）

侦察阶段验证：如果 API 端点也 412，该路由标 `"waf_blocked": true`，查询阶段走 1.5（WAF token 周期刷新，不是每次查询开 CDP）。

### WAF token TTL 机制

WAF 的放行凭证（通常是个 cookie）有有效期。侦察阶段观察：
- 如果 WAF token 有效期 ≥ 30 分钟 → 412 时 CDP 刷一次 cookie 写回 storage_state，后续查询继续走 HTTP
- 如果 WAF token 有效期 < 5 分钟（极短）→ 该路由标 `"waf_blocked": true, "cdp_only": true`，每次都得开浏览器

## cookie：storage_state（不是复制 Cookies 文件）

旧方案（已废弃）：复制 Chrome 的 `Cookies` SQLite + `Local State` 到临时 profile。
新方案：Playwright `storage_state` 导出 JSON。

```python
# 导出（一次性，或 cookie 过期时重跑）
ctx.storage_state(path="~/.openclaw-lzu/portal_state.json")
```

`portal_state.json` 含 cookies + localStorage（比纯 Cookies 文件全，部分 LZU 前端用 localStorage 存 token）。

查询阶段读 cookie：
```python
import json, os
state = json.load(open(os.path.expanduser("~/.openclaw-lzu/portal_state.json")))
cookies = {c["name"]: c["value"] for c in state["cookies"] if "lzu.edu.cn" in c.get("domain", "")}
s = requests.Session()
s.cookies.update(cookies)
```

cookie 过期标志：requests 返 302 重定向到 `sso.lzu.edu.cn` 或 401 → 提示用户重跑 storage_state 导出。

## 服务目录 + 通用登录（FasterLZU 逆向）

### 核心发现

FasterLZU 有一个**通用的服务免密登录机制**，不是单个功能接口：

1. **服务目录接口**：`getServiceInfoDetailByTerminalRole`，返回全校所有接入服务（含 `h5_url` + `service_info_id`）
2. **通用登录逻辑**：拿 `service_info_id` 换 ST，直接拼进 `h5_url` 免密打开
   ```dart
   final st = await authRepository.getSt(service.service_info_id!);
   url = '$url?PersonID=$personID&st=$st&ticket=$st';
   ```

### 影响

- 之前假设"每个服务要单独侦察登录方式"，现在发现**登录这一步可以对全部服务批量解决**
- 侦察阶段可以专注"数据怎么取"，不用重复解决"怎么登录"
- 只解决登录，不解决取数据。已逆向的服务能直接拿 JSON；其余服务只能做到"自动打开网页"

### 三步批量登录

```
Step 1: 登录 → 拿 loginToken
   POST https://appservice.lzu.edu.cn/api/eusp-unify-terminal/app-user/login
   body: {"app_os": 2, "name": "学号", "pwd": "密码"}
   返回: {"data": {"login_token": "xxx", "gateway_token": "xxx"}}

Step 2: 拿服务目录
   POST https://my.lzu.edu.cn/api/eusp-terminal-management/api/v2/getServiceInfoDetailByTerminalRole
   body: AES 加密("terminalId=1&loginToken=xxx")
   返回: 全部服务的 h5_url + service_info_id

Step 3: 拿 ST → 免密打开
   GET https://my.lzu.edu.cn/api/eusp-unify-terminal/app-user/getSt?loginToken=xxx&serviceId=yyy
   返回: ST ticket
   拼接: h5_url?PersonID=学号&st=ST&ticket=ST
```

### gateway_token 有效期与缓存

**gateway_token 从登录接口获取，用于数据接口的 Authorization header**：
- 格式：`ST-xxxxx-xxxxx`（Service Ticket）
- 有效期：至少 5 分钟（实测），可能更长
- 缓存策略：登录后缓存 gateway_token，后续请求直接用，不需要每次重新登录
- 过期标志：接口返回 `code=10004`（未登录）或 `401` → 重新登录获取新 token

**认证方式对比**：
| 用途 | 认证方式 | 有效期 |
|---|---|---|
| 服务目录 / ST 获取 | loginToken | 长期（登录后有效） |
| 数据接口（空教室/成绩等） | gateway_token（Authorization header） | 至少5分钟，可能更长 |
| 免密打开网页 | ST ticket | 一次性（每次打开需要新 ST） |

### routes.json 中的基础路由

以 `_` 前缀标记的基础路由（不直接查询，供其他路由依赖）：

| 路由名 | type | 用途 |
|---|---|---|
| `_login` | auth_step | 登录拿 loginToken |
| `_service_catalog` | service_catalog | 拿服务目录 |
| `_get_st` | auth_step | 拿 ST ticket |

### 新的侦察流程

```
旧流程（每个服务单独侦察登录）：
  服务A → 侦察怎么登录 → 侦察怎么取数据
  服务B → 侦察怎么登录 → 侦察怎么取数据
  服务C → 侦察怎么登录 → 侦察怎么取数据
  ...重复 N 次

新流程（登录批量解决）：
  Step 1: 一次性拿 loginToken + 服务目录
  Step 2: 批量确认哪些服务能免密打开
  Step 3: 对每个服务，只侦察"数据怎么取"
```

## routes.json 完整字段

### 单步任务（简单查询）

```json
{
  "查校园卡余额": {
    "is_write": false,
    "steps": [
      {
        "endpoint": "https://my.lzu.edu.cn/lzu_grgzt_fx/api/card/balance",
        "method": "GET",
        "params": {},
        "headers_extra": {"X-Requested-With": "XMLHttpRequest"},
        "auth": "portal_session",
        "response_path": "data.balance",
        "response_type": "float"
      }
    ],
    "notes": "余额单位元；同一接口还返回 data.flow/data.books",
    "discovered": "2026-07-03",
    "last_ok": "2026-07-03",
    "fail_log": {"401": 0, "412": 0, "schema_mismatch": 0, "timeout": 0}
  }
}
```

### 多步依赖链（先取学期 ID，再查考试列表）

```json
{
  "查考试安排": {
    "is_write": false,
    "steps": [
      {
        "endpoint": "https://my.lzu.edu.cn/.../api/term/current",
        "method": "GET",
        "params": {},
        "auth": "portal_session",
        "response_path": "data.termId",
        "save_as": "term_id"
      },
      {
        "endpoint": "https://my.lzu.edu.cn/.../api/exam/list",
        "method": "GET",
        "params": {"termId": "${step:term_id}", "pageNo": 1, "pageSize": 50},
        "auth": "portal_session",
        "response_path": "data.list",
        "response_type": "list"
      }
    ],
    "notes": "xqdm=学期代码；返回考试科目/考场/时间",
    "discovered": "2026-07-03",
    "last_ok": "2026-07-03",
    "fail_log": {"401": 0, "412": 0, "schema_mismatch": 0, "timeout": 0}
  }
}
```

### 写操作（需确认）

```json
{
  "提交场馆预约": {
    "is_write": true,
    "require_confirm": true,
    "steps": [
      {
        "endpoint": "https://my.lzu.edu.cn/.../api/venue/book",
        "method": "POST",
        "params": {"venueId": "${user_input:venue_id}", "timeSlot": "${user_input:slot}"},
        "auth": "portal_session",
        "response_path": "data.success",
        "response_type": "bool"
      }
    ],
    "notes": "提交前 agent 必须向用户复述场地+时段并取得确认",
    "discovered": "2026-07-03",
    "last_ok": "2026-07-03",
    "fail_log": {"401": 0, "412": 0, "schema_mismatch": 0, "timeout": 0}
  }
}
```

### 字段说明

| 字段 | 说明 |
|---|---|
| `type` | (可选) 路由类型：`service_catalog`=服务目录；`auth_step`=认证步骤；`data_query`=数据查询（默认） |
| `is_write` | `false`=只读查询；`true`=会产生副作用（提交/修改/删除），必须确认 |
| `require_confirm` | `true` 时 agent 执行前必须向用户复述操作内容并取得确认 |
| `steps` | API 调用数组。单步任务长度 1，多步依赖链按顺序执行，前一步 `save_as` 的值注入后一步 params |
| `endpoint` | 侦察抓到的真实 API URL |
| `method` | GET / POST |
| `params` | GET 的 query params 或 POST 的 body。动态值用占位符（见下） |
| `headers_extra` | 非标准请求头。标准头（Cookie/User-Agent）自动注入不记 |
| `auth` | `"portal_session"` = 从 storage_state 读 cookie；`"loginToken"` = 用 loginToken 鉴权；`"none"` = 无需鉴权 |
| `response_path` | jq-like 路径取数据（`data.balance` / `data.list`） |
| `response_type` | `float` / `int` / `str` / `list` / `dict` / `bool` |
| `save_as` | (多步链) 该步输出存为变量名，供后续 step 引用 |
| `notes` | 人类可读备注 |
| `discovered` / `last_ok` | 侦察日期 / 上次成功日期 |
| `fail_log` | 按失败原因分类计数（见下） |
| `waf_blocked` | (可选) API 也走 WAF，查询走 1.5 周期刷新 |
| `cdp_only` | (可选) 必须 CDP（纯前端无 API / JS 计算的 token） |

### 动态参数占位符

| 占位符 | 含义 | agent 怎么填 |
|---|---|---|
| `${user_input:month}` | 用户对话里给的 | 问用户"查哪个月的账单？" |
| `${compute:current_term}` | 运行时计算的 | 调函数算当前学期代码 |
| `${step:term_id}` | 前一步 `save_as` 的值 | 从依赖链前序 step 取 |
| `${compute:today}` | 运行时计算的 | 今天日期 YYYY-MM-DD |
| `${loginToken}` | 登录获取的 token | 从 `_login` 路由获取 |
| `${service_info_id}` | 服务目录里的服务 ID | 从 `_service_catalog` 路由获取 |
| `${username}` / `${password}` | 用户凭据 | 从 config.json 或环境变量获取 |

### fail_log 失败分类 + 动作矩阵

| fail_log key | 触发条件 | 动作 |
|---|---|---|
| `401` | HTTP 401 或 302→sso | cookie 过期 → 提示用户重导 storage_state（**不触发重新侦察**） |
| `412` | HTTP 412 | WAF 拦截 → 走 1.5 刷新 WAF token（**不代表接口失效**） |
| `schema_mismatch` | 200 但 `response_path` 取不到值 | 接口改版 → 计数≥2 触发走 2 重新侦察 |
| `timeout` | 请求超时 | 网络抖动 → 重试 1 次，再失败走 2 |

### EasyTong 校园卡密钥

EasyTong 校园卡查询需要 MD5 签名。密钥从 FasterLZU app.js 逆向获得：
- `MD5KeyYm = ok15we1@oid8x5afd@`
- 签名方式：`MD5(AccNum|Time|MD5KeyYm)` 或 `MD5(AccNum|EPID|Time|MD5KeyYm)`

### 空教室 skjc 字段

空教室查询返回的 `skjc` 字段是 14 位二进制字符串，对应节次：
- 位置 0-13 对应：`1, 2, 3, 4, 中, 中2, 5, 6, 7, 8, 9, 10, 11, 12`
- **`1` = 空闲，`0` = 占用**（注意：不是 0=空闲）

## 侦察脚本契约（skills/portal/recon.py）

### 新侦察流程（登录/取数据分离）

```
Phase 1: 登录 + 服务目录（一次性）
1. agent 调: python3 skills/portal/recon.py --init
2. 脚本提示用户输入学号密码（或从 config.json 读取）
3. 调用 _login 接口获取 loginToken
4. 调用 _service_catalog 接口获取全部服务列表
5. 落盘到 routes.json 的 _login 和 _service_catalog

Phase 2: 批量确认哪些服务能免密打开
6. 遍历服务目录，对每个服务：
   - 调用 _get_st 获取 ST ticket
   - 拼接 h5_url?PersonID=&st=&ticket=
   - 尝试 requests.get 验证能否打开
   - 记录结果：能打开 / 需要额外登录 / 403
7. 输出报告：哪些服务能免密打开，哪些不能

Phase 3: 逐个侦察数据接口（按需）
8. agent 调: python3 skills/portal/recon.py "查校园卡余额"
9. 脚本检查：该服务是否在服务目录里？
   - 在 → 用 loginToken + ST 打开 h5_url
   - 不在 → 用 SSO cookie 打开
10. CDP 监听用户操作，抓 API，回放验证，落盘
```

### 旧侦察流程（兼容）

```
1. agent 调: python3 skills/portal/recon.py "查校园卡余额"
2. 脚本启动 Playwright（headless=False），打开 my.lzu.edu.cn
3. 脚本提示用户："请在浏览器里点一下【校园卡】，我在监听网络请求"
4. 脚本 page.on("response") 监听所有 *.lzu.edu.cn 的 XHR/Fetch
5. 用户手动点击 → 脚本捕获该次操作触发的所有 JSON 请求
6. 脚本打印抓到的请求清单（支持多选 —— 一次点击可能触发多个接口）
7. 用户选要记的（可多选，构成依赖链）
8. 如果是列表分页功能 → 脚本引导用户点两次（第一页 + 下一页），diff 参数找页码字段
9. 脚本用 requests 自动回放验证（确认不是临时凭证，能拿到同样数据）
10. 验证通过 → 写入 routes.json + 导出 storage_state 刷新 cookie
11. headers 打印时脱敏（token 类只显示末 4 位）
```

### 请求过滤规则（自动排除噪音）

排除：
- 静态资源：`.js` / `.css` / `.png` / `.jpg` / `.woff` / `.ico` / `.svg`
- 第三方：非 `*.lzu.edu.cn` 域名
- 页面入口：`.html` / `.htm` / `.jsp`（只留 API）
- WAF challenge：URL 含 `/ofolQLH6ypbp/` 或 `$_ts` 的请求

保留：
- XHR/Fetch 请求（`resource_type` 为 `xhr` 或 `fetch`）
- 返回 JSON 的请求

### 多选 + 依赖链发现

一次点击可能触发多个 JSON 请求（如先请求用户信息再请求列表）。脚本打印所有捕获的请求，用户**可多选**要记录的。多选的请求自动构成 `steps` 数组（按触发顺序排列），前一个的 `response_path` + `save_as` 可手动标注为后一个的 `params` 依赖。

### 分页参数自动发现

如果目标功能有列表分页，脚本引导用户操作两次：
1. 看第一页
2. 点"下一页"

脚本 diff 两次请求的参数差异，自动标注"变化的字段 = 页码/偏移量"，写入 `params` 时标 `${user_input:page}` 或固定默认值。

### 落盘前自动回放验证

抓到请求后，脚本**当场用 requests 原样打一遍**（带同一 cookie）：
- 拿到相同数据 → 确认不是临时凭证，写入 routes.json
- 拿不到 / 401 / 结构不同 → 警告"该请求可能依赖临时凭证，不适合走 HTTP"，建议标 `cdp_only`

### headers 脱敏

打印 headers 确认时，`Authorization` / `X-Token` / `Cookie` 等敏感字段只显示末 4 位：
```
Authorization: Bearer ****xxxx
Cookie: iPlanetDirectoryPro=****UT09; ...
```

### 侦察脚本核心逻辑（伪码）

```python
captured = []

def on_response(response):
    url = response.url
    if "lzu.edu.cn" not in url: return
    if response.request.resource_type not in ("xhr", "fetch"): return
    if any(url.endswith(ext) for ext in (".js",".css",".png",".jpg",".woff",".ico")): return
    try:
        body = response.json()
    except: return
    req = response.request
    captured.append({
        "url": url,
        "method": req.method,
        "params": parse_qs(urlparse(url).query) if req.method == "GET" else None,
        "post_data": req.post_data,
        "headers": mask_sensitive(req.headers),
        "status": response.status,
        "response_body": body,
    })

page.on("response", on_response)
# 等用户操作...
# 用户多选 → 自动回放验证 → 写入 routes.json
```

## 门户 DOM 结构（侦察 + CDP 兜底参考）

- 首页内容在**父页 DOM**（不是 iframe），Element UI（`.el-carousel` 轮播收藏入口）
- 2 个 iframe：`lzu_grgzt_fx/#/index`（主应用）、`lzu_xzgw_xzgw/#/xzgw`（行政公务）
- 我的收藏入口 DOM：`.el-carousel__item .home-app-item`，文本在 `.tmic_name` / `.home-name`
- 考试安排等收藏项**在父页**，点击可能触发：① 父页 AJAX 加载 ② iframe 导航 ③ `window.open` 新 tab
- Element UI 组件：`.el-table` / `.el-dialog` / `.el-button` / `.el-tabs`（参照 `lzu-course-evaluation` skill）

## CDP 兜底场景

只有以下情况走 CDP（记录 `"cdp_only": true`）：
1. 动态 token 是 JS 运行时计算，HTML 抠不到（走完第 3 级 HTML 抠取仍失败）
2. 功能纯前端计算无后端 API
3. 写操作需要复杂交互（多步表单 + 验证码），API 单靠 POST 无法完成

CDP 兜底时：`p.chromium.connect_over_cdp("http://127.0.0.1:9222")` 接已登录的 Chrome 驱动操作。

## 不可妥协的约束

- **登录和取数据分离**。loginToken 是通用的，一次登录批量解决所有服务的免密打开；取数据是每个服务单独的
- **gateway_token 缓存复用**。登录后缓存 gateway_token（至少 5 分钟有效），后续请求直接用，不需要每次重新登录。过期时重新登录
- **查询阶段不开浏览器**。routes.json 有路由且 cookie/loginToken/gateway_token 有效时必须走 requests
- **侦察脚本只监听不自动点击**。用户手动点，脚本抓网络请求
- **落盘前必须自动回放验证**。不验证就写入 = 几天后查询失败才发现是临时凭证
- **写操作必须确认**。`is_write: true` + `require_confirm: true` 时不确认不执行
- **storage_state 而非 Cookies 文件**。用 Playwright 原生格式，不碰 Chrome profile
- **fail_log 按原因拆分**。401 不等于接口坏了，412 不等于接口失效，不能混在一个计数器
- **headers 脱敏打印**。终端/截图不能泄露完整 session token
- **loginToken 不落盘明文**。存在内存或加密存储，不写进 routes.json
