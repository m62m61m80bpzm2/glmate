# glmate

轻量 QQ 群 AI 机器人，基于 **智谱 GLM-4 全家桶**（对话 / 画图 / 视频 / 读图 / 搜索），直连 [SnowLuma](https://github.com/LlamaFamily/SnowLuma) 的 OneBot v11 WS 服务器，零依赖中间件。

## 特性

- **多模态体感**：发图自动 OCR、@机器人 画图/视频 直接以媒体形式出现
- **429 静默重试**：LLM 与工具调用均支持 10 档阶梯重试，耗尽才在群里报一次 `[429]`
- **上下文压缩**：超出窗口 × 80% 自动把旧消息摘要，保留最近对话细节
- **多群独立会话**：每群有独立 session 树，`/new` `/rename` `/use` 互不干扰
- **管理员命令**：`/on` `/off` `/sessions` `/context` `/reset`，botQQ 即管理员
- **自 @ 触发**：管理员用自己的号 @ 机器人也能收到回复（方便单人测试）

## 架构

```
QQ 用户 ──→ SnowLuma (OneBot v11 WS :3001)
                      ↓
                 glmate (Python 3.10+)
                      ↓
              智谱 GLM API (bigmodel.cn)
                 ├─ glm-4.7-flash    主模型 (200K)
                 ├─ glm-4-flash      备用 (128K)
                 ├─ glm-4.1v-thinking-flash  读图
                 ├─ cogview-3-flash  画图
                 └─ cogvideox-flash  生成视频
                 └─ web_search       ddgs 搜索引擎
```

## 前置条件

1. **Python 3.10+**（推荐 `py -3` 或 `python` 在 PATH 里）
2. **SnowLuma**（[LlamaFamily/SnowLuma](https://github.com/LlamaFamily/SnowLuma)，QQ 协议端，需扫码登录你的 QQ）—— 自行下载编译版，解压到本项目根目录
3. **智谱 API Key** —— [bigmodel.cn 控制台](https://bigmodel.cn) 申请免费额度，复制 `apiKey`
4. **Bot QQ 号**（你用来跑机器人的 QQ）
5. **想接入的群号**（先在 QQ 群设置里复制群号，再让机器人进群）

## 部署

### 1. 安装依赖

```bash
cd zglmbot
pip install -r requirements.txt
```

### 2. 配置

```bash
cd ..
cp zglmbot/config.example.json zglmbot/config.json
```

编辑 `zglmbot/config.json`，按下面表格填写：

| 字段 | 含义 | 去哪填 |
|---|---|---|
| `botQQ` | 机器人的 QQ 号（即你扫码登录的那个号） | 自己 |
| `apiKey` | 智谱开放平台 API Key | [bigmodel.cn](https://bigmodel.cn) 控制台 |
| `adminQQs` | 管理员列表（默认填 botQQ 即可） | 同上 |
| `snowluma.accessToken` | **WS:3001 的 token**，注意和 HTTP:3000 不同 | 见下方查询方法 |
| `allow.groups` | 要接入的群号列表（如 `[123456789]`） | QQ 群设置 → 群号 |
| `model` / `backupModel` | 主/备模型，一般不改 | — |

**⚠️ 常见坑：** SnowLuma 的 `config/onebot_*.json` 里有两个 `accessToken`：
- `httpServers[0].accessToken` → HTTP :3000 接口用
- `wsServers[0].accessToken` → **WS :3001 接口用（填这个！）**
两者经常不一样，填错了机器人连不上。

### 3. 启动 SnowLuma

双击运行解压目录下的主程序，扫码登录 QQ。启动后会在 `http://127.0.0.1:5099` 打开管理页，确认 Web UI 正常 + `wsServers[0].reportSelfMessage` 设为 `true`。

### 4. 启动机器人

```bash
# 方式 A：一键启动（SnowLuma 隐藏 + glmate 单窗口）
.\start-all.bat

# 方式 B：只启动机器人（SnowLuma 已单独开着）
cd zglmbot
.\run.bat
```

### 5. 验证

在配置的群里 @机器人，发：
- 文本："你好"
- 带图："看看这张图里写了啥"
- 画图："画一只猫"
- 视频："生成一段狗狗跑步的视频"

## 命令

| 命令 | 权限 | 说明 |
|---|---|---|
| `/help` | 所有人 | 显示帮助 |
| `/sessions` | 所有人 | 查看当前群会话列表 |
| `/new` | 所有人 | 新建会话 |
| `/rename <名>` | 所有人 | 重命名当前会话 |
| `/use <编号>` | 所有人 | 切换到指定会话 |
| `/context` | 所有人 | 查看当前上下文 token 用量 |
| `/reset` | 管理员 | 清空当前会话（会回滚） |
| `/on` | 管理员 | 开启机器人响应 |
| `/off` | 管理员 | 关闭机器人响应 |

## 配置字段详解

完整字段见 `zglmbot/config.example.json`，关键节选：

- `requireMention`：`true` 时需 @机器人 才回复；`false` 时任意发言都触发
- `webSearch.numResults`：搜索条数（默认 5）
- `compressRatio`：触发压缩的阈值比例（默认 0.8 = 窗口 80%）
- `compressKeepRecentTokens`：压缩时保留最近多少 token 不清理（默认 3000）
- `contextWindows`：各模型的 token 窗口，按智谱官方文档填写

## 故障排查

- **机器人不回复**：先看 `zglmbot/state/zglmbot.log`，确认 WS 已连接（`已连接 SnowLuma OneBot WS`）
- **429 限流提示**：智谱免费额度限流正常，等几分钟重试即可
- **图片发不出去**：检查 SnowLuma `wsServers.accessToken` 填的是 **3001 的** 不是 3000 的
- **自 @ 不触发**：确认 SnowLuma `reportSelfMessage: true`

## 开源协议

MIT — 详见 `LICENSE`。
本软件仅供学习研究，使用风险由用户自行承担。QQ 机器人行为受腾讯政策约束，请遵守相关规范。
