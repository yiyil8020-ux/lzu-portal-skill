# lzu-portal-skill

兰州大学个人工作台（my.lzu.edu.cn）查东西用的 skill。

## 能查啥

- 校园卡余额
- 成绩
- 课表
- 考试安排
- 空教室
- 消息通知
- ...还有 30 多个服务

## 支持的 AI 助手

这个 skill 适用于任何能跑 shell 的 AI 助手：

| 助手 | 状态 |
|---|---|
| OpenClaw | ✅ |
| OpenCode | ✅ |
| Claude Code | ✅ |
| Codex | ✅ |
| Cursor | ✅ |
| Gemini CLI | ✅ |
| 其他能跑 shell 的助手 | ✅ |

## 安装

```bash
pip install requests
```

## 怎么用

```bash
# 第一步：导出登录信息（一次性）
python3 cli.py auth
```

> **初始配置**：需要填写你的学号和兰大 App 登录密码。这些信息只会保存在本地（`~/.openclaw-lzu/` 目录），不会上传到任何服务器，不用担心安全问题。

```bash
# 查东西
python3 cli.py query "查校园卡余额"
python3 cli.py query "查成绩" --param xn=2025 --param xqm=1
python3 cli.py query "查空教室" --param xqh=02 --param jxlh=02010017 --param slot=下午

# 看看都有哪些功能
python3 cli.py routes

# 检查状态
python3 cli.py status
```

## 原理

首次用某个功能时，需要"侦察"一下（抓一下 API 接口）。侦察成功后，下次就直接调接口，不用再开浏览器了。

## 文件说明

```
portal-skill/
├── cli.py            # 命令行入口
├── query.py          # 查询引擎
├── easytong.py       # 校园卡查询
├── classroom.py      # 空教室查询
├── auth.py           # 登录信息导出
├── recon.py          # 侦察脚本
├── SKILL.md          # Skill 定义
└── AGENTS.md         # Agent 指令
```

## 注意事项

- 登录信息会过期，过期了重新导出一下就行
- 场馆预约之类的功能需要开浏览器
- 空教室查询：`1` 表示空闲，`0` 表示占用
