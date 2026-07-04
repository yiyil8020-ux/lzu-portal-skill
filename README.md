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

## 已验证功能

| 功能 | 认证方式 | 状态 |
|---|---|---|
| 查用户信息 | gateway_token | ✅ |
| 查未读消息 | gateway_token | ✅ |
| 查最新通知 | gateway_token | ✅ |
| 查空教室 | gateway_token | ✅ |
| 查成绩 | gateway_token | ✅ |
| 查课表 | gateway_token | ✅ |
| 查学期信息 | gateway_token | ✅ |
| 查本周课表 | gateway_token | ✅ |
| 查考试安排 | gateway_token | ✅ |
| 查校园卡余额 | EasyTong ET token | ✅ |
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
