# lzu-portal-skill

兰州大学个人工作台（my.lzu.edu.cn）通用操作 skill —— 侦察/查询分离架构。

## 功能

- 查询校园卡余额、成绩、课表、考试安排、空教室、消息通知等 35+ 个服务
- 纯 HTTP 查询（不开浏览器），gateway_token 认证
- 路由记忆：首次侦察，后续直接 replay
- EasyTong 校园卡：MD5 签名 + 4 步认证链

## 安装

```bash
pip install requests
```

## 使用

```bash
# 导出 cookie（一次性）
python3 cli.py auth

# 侦察一个功能
python3 cli.py recon "查校园卡余额"

# 查询（纯 HTTP）
python3 cli.py query "查校园卡余额"
python3 cli.py query "查成绩" --param xn=2025 --param xqm=1

# 空教室查询（自动解析 skjc 字段）
python3 cli.py query "查空教室" --param xqh=02 --param jxlh=02010017 --param slot=下午
python3 cli.py query "查空教室" --param xqh=02 --param jxlh=02010017 --param date=2026-07-07

# 列出所有路由
python3 cli.py routes

# 检查状态
python3 cli.py status
```

## 路由记忆

首次查询某功能时，需要侦察（抓 API 接口）。侦察成功后，路由记录到 `~/.openclaw-lzu/portal_routes.json`。后续查询直接 replay，不开浏览器。

## 认证

- **SSO cookie**：浏览器登录 my.lzu.edu.cn 后导出
- **gateway_token**：API 登录获取，用于大部分查询
- **EasyTong ET token**：校园卡查询专用，需 MD5 签名

## 对话示例

### 查考试安排

```
用户: 本学期考试安排是什么？
助手: 正在查询考试安排...
助手: 本学期考试安排：
| 科目 | 时间 | 地点 |
|---|---|---|
| 高等数学 | 2026-06-20 09:00 | 天山堂A101 |
| 大学英语 | 2026-06-22 14:00 | 医学校区B201 |
| ... | ... | ... |
```

### 查空教室

```
用户: 明天下午天山堂A区有空教室吗？
助手: 正在查询空教室...（需要校区代码和教学楼代码）
助手: 请告诉我校区和教学楼的完整名称，例如：
  - 校区：榆中校区（代码 02）
  - 教学楼：天山堂（代码 02010017）
用户: 榆中校区 天山堂
助手: 2026-07-05 下午 天山堂 空教室：
| 教室 | 节次 | 状态 |
|---|---|---|
| 天山堂A101 | 5,6,7,8 | ✅ 空闲 |
| 天山堂A201 | 5,6 | ✅ 空闲 |
| 天山堂A301 | 7,8 | ✅ 空闲 |
```

### 查校园卡余额

```
用户: 我校园卡还有多少钱？
助手: 正在查询校园卡余额...（需要 EasyTong 认证）
助手: 校园卡余额：￥125.50
```

### 查课表

```
用户: 这周课表
助手: 正在查询本周课表...（第 18 周）
助手: 本周课表（第 18 周）：
| 时间 | 周一 | 周二 | 周三 | 周四 | 周五 |
|---|---|---|---|---|---|
| 1-2 节 | 高等数学 | | | | |
| 3-4 节 | | 大学英语 | | | |
| 5-6 节 | | | | 体育 | |
| 7-8 节 | | | | | |
```

### 问校车时间

```
用户: 今天下午有校车吗？
助手: 校车发车时间表：
  - 固定发车时间：08:00, 10:00, 12:00, 14:00, 16:00, 18:00
  - 其他时间：坐满即发（等待约 15 分钟）
  
  今天下午固定发车时间：14:00, 16:00
  
  如果错过固定发车时间，可以去站点等坐满即发。
```

### 查用户信息

```
用户: 我是谁？
助手: 正在查询用户信息...
助手: 用户信息：
  - 学号：2024001234
  - 姓名：张三
  - 学院：信息科学与工程学院
```

## 已验证功能

| 功能 | 查询方式 | 状态 |
|---|---|---|
| 查用户信息 | gateway_token | ✅ |
| 查未读消息 | gateway_token | ✅ |
| 查最新通知 | gateway_token | ✅ |
| **查空教室** | **gateway_token + skjc 解析** | ✅ |
| 查成绩 | gateway_token | ✅ |
| 查课表 | gateway_token | ✅ |
| 查学期信息 | gateway_token | ✅ |
| 查本周课表 | gateway_token | ✅ |
| 查考试安排 | gateway_token + ST + accessTokenBySt | ✅ |
| **查校园卡余额** | **EasyTong ET token + MD5 签名** | ✅ |
| 查服务时间 | 静态数据 | ✅ |
| 32 个服务打开 | 免密打开 | ✅ |

## 文件结构

```
portal-skill/
├── SKILL.md          # Skill 定义（触发词、使用方法）
├── AGENTS.md         # Agent 指令（认证、WAF、路由结构）
├── cli.py            # CLI 入口
├── query.py          # 查询引擎
├── easytong.py       # EasyTong 校园卡查询 helper
├── classroom.py      # 空教室查询 helper（skjc 解析）
├── auth.py           # storage_state 导出
├── recon.py          # 侦察脚本
└── __init__.py
```

## 依赖

- requests
- playwright（可选，用于认证导出和侦察）
- cryptography（可选，用于 AES 加密）

## 注意事项

- SSO cookie 会过期，需要重新导出
- gateway_token 有效期至少 5 分钟
- EasyTong 密钥：`ok15we1@oid8x5afd@`（从 FasterLZU 逆向）
- 场馆预约等 WAF 保护的功能需要 CDP
- 空教室 `skjc` 字段：`1=空闲，0=占用`（14 位，对应 1,2,3,4,中,中2,5,6,7,8,9,10,11,12 节）
