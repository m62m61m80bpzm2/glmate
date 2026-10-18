# glmate

轻量 QQ 群 AI 机器人，默认接 **智谱 GLM-4 全家桶**（对话 / 画图 / 视频 / 读图 / 搜索），直连 [SnowLuma](https://github.com/LlamaFamily/SnowLuma) 的 OneBot v11 WS 服务器，零依赖中间件。对话与读图走标准 OpenAI `chat/completions`，所以换成任意 OpenAI 兼容端点也能跑。

> **部署方式**：本 README 是按「可被 AI agent 逐步执行」的順序写的。把仓库地址发给你的 AI 编程助手，让它读完本文件后代你完成安装、配置、启动和排错，通常不需要人工读源码。详见文末「交给 AI agent 部署」。

## 特性

- **多模态体感**：发图自动 OCR、@机器人 画图/视频 直接以媒体形式出现
- **端点可替换**：`baseUrl` 指向任意 OpenAI 兼容服务；能力按配置自动裁剪，未开启的工具不会注册
- **429 静默重试**：LLM 与工具调用均支持 10 档阶梯重试，耗尽才在群里报一次 `[429]`
- **上下文压缩**：超出窗口 × 80% 自动把旧消息摘要，保留最近对话细节
- **多群独立会话**：每群有独立 session 树，`/new` `/rename` `/use` 互不干扰
- **管理员命令**：`/on` `/off` `/sessions` `/context` `/reset`，botQQ 即管理员
- **自 @ 触发**：管理员用自己的号 @ 机器人也能收到回复（方便单人测试），需要 SnowLuma `reportSelfMessage: true`，它出厂默认是 `false`

## 架构

```
QQ 用户 ──→ SnowLuma (OneBot v11 WS :3001)
                      ↓
                 glmate (Python 3.10+)
                      ↓
          {baseUrl}  ── 默认智谱 GLM API (bigmodel.cn)，可换 OpenAI 兼容端点
                 ├─ glm-4.7-flash    主模型 (200K)   ← /chat/completions，通用
                 ├─ glm-4-flash      备用 (128K)     ← /chat/completions，通用
                 ├─ glm-4.1v-thinking-flash  读图    ← /chat/completions，通用
                 ├─ cogview-3-flash  画图            ← /images/generations，智谱风格
                 ├─ cogvideox-flash  生成视频        ← /videos/generations，智谱专有
                 └─ web_search       ddgs 搜索引擎   ← 本地抓取，与端点无关
```

## 文件位置与依赖

**先说最容易卡住的一步：依赖清单不在仓库根目录，在 `zglmbot/requirements.txt`。**

克隆下来仓库里只有这些文件（`SnowLuma/` 需要自己下载，不在库里）：

```
glmate/
├─ README.md               ← 本文件
├─ LICENSE                 ← MIT
├─ requirements.txt        ← 只有一行 -r zglmbot/requirements.txt，方便在根目录直接装
├─ start-all.bat           ← 一键启动（隐藏 SnowLuma + 机器人控制台）
└─ zglmbot/                ← 全部 Python 代码都在这个子目录
   ├─ zglmbot.py           ← ★ 机器人本体，约 1200 行，单文件即可运行
   ├─ requirements.txt     ← ★ 真正的依赖清单
   ├─ config.example.json  ← 配置模板，复制成 config.json 再填
   ├─ run.bat              ← 单独启动机器人
   ├─ state/               ← 运行时自动生成：zglmbot.log、sessions.json
   ├─ zagent37a.py         ← 上游参考实现，zglmbot.py 由它精简而来，运行时不 import
   └─ zagent-config-ref.json ← 同上，仅参考
```

依赖只有两个第三方包，其余全用 Python 标准库：

| 包 | 版本要求 | 用途 | 是否必需 |
|---|---|---|---|
| `websockets` | >= 12.0 | 连接 SnowLuma 的 OneBot v11 WS | 必需，缺失时启动即退出 |
| `ddgs` | >= 8.0.0 | `web_search` 工具的本地搜索 | 惰性 import，只在做搜索时才需要 |

也就是说：只要 `pip install websockets` 就能把机器人跑起来对话；不装 `ddgs`、并把 `webSearch.enabled` 设为 `false`，搜索工具会自动不注册。

安装（Python 3.10+，两种方式等价）：

```bash
pip install -r zglmbot/requirements.txt    # 指定真实清单
pip install -r requirements.txt           # 根目录转发，等价
```

装完先自检，再启动：

```bash
python -c "import websockets, ddgs; print('deps OK', websockets.__version__)"
```

**多 Python 环境的坑**：`run.bat` 和 `start-all.bat` 都是先找 PATH 里的 `python`，找不到才退回 `py -3`。如果机器上装了多个 Python，依赖必须装进真正执行脚本的那个解释器，否则自检通过、启动时却报 `ModuleNotFoundError: No module named 'websockets'`。用完整路径最稳：

```bash
D:\your\python.exe -m pip install -r zglmbot\requirements.txt
D:\your\python.exe zglmbot\zglmbot.py
```

缺依赖时 `zglmbot.py` 会直接打印「缺少依赖 websockets，请先运行: pip install -r requirements.txt」后退出，日志里不会有栈。

不入库的目录与文件：`SnowLuma/`（协议端，自行下载解压到根目录）、`qq-bridge/`、`make-your-qq-ai-group-member/`、`zglmbot/config.json`（含 API Key 和 token）、`zglmbot/state/`（日志与会话）。

## 前置条件

1. **Python 3.10+**（推荐 `py -3` 或 `python` 在 PATH 里）
2. **SnowLuma**（[LlamaFamily/SnowLuma](https://github.com/LlamaFamily/SnowLuma)，QQ 协议端，通过注入已登录的 QQ 桌面进程工作）—— 自行下载编译版，解压到本项目根目录的 `SnowLuma/`。解压完整性可以这样自检：

   ```
   SnowLuma/
   ├─ node.exe              ← 运行时
   ├─ index.mjs             ← ★ 主程序入口，缺了 start-all.bat 起不来
   ├─ native/*.node         ← QQ 协议与 WS 原生插件
   ├─ config/onebot_*.json  ← 在 WebUI 启用 OneBot 后才生成，accessToken 从这里取
   └─ data/<QQ号>/          ← 登录后的本地数据
   ```

   只有 `node.exe` 和 `native/`、没有 `index.mjs`，说明解压不完整（常见于只拷了部分文件）；重新解压整个压缩包即可。

   ⚠️ **`SnowLuma/` 被 gitignore 了，`git clean -fdX` 会把整个协议端删掉**（发布分支、清理工作区时最容易误伤），删完只剩手动保留下来的 `data/`。要清理工作区时先确认这一点，或者把 SnowLuma 移到仓库外面。恢复方式：重新下载 `SnowLuma-v<版本>-win-x64.zip` 解压进 `SnowLuma/`，`data/<QQ号>/` 还在的话不用重新登录注入。
3. **API Key** —— 默认用 [bigmodel.cn 控制台](https://bigmodel.cn) 的免费额度；也可以换成任意 OpenAI 兼容端点（见「换非智谱端点」）
4. **Bot QQ 号**（⚠️ **强烈建议用专门的小号，不要用主号**，理由见下方「账号安全」）
5. **想接入的群号**（先在 QQ 群设置里复制群号，再让机器人进群）

## ⚠️ 账号安全（必读）

SnowLuma / NapCat 这类第三方协议端**不在腾讯授权范围内**，属于灰色地带：

- **官方明面上只允许** [QQ 机器人开放平台](https://q.qq.com)（频道/官方 bot），第三方方案违反 QQ 用户协议
- **实际表现**：2026 年腾讯明显收紧了反机器人策略——常见后果不是立刻封号，而是**定期被踢下线**（几小时到一天一次）、提示"设备存在外挂或其他软件影响 QQ 正常使用"，少数情况触发冻结
- **老号比新号好**，新号风险最高

### 降低风险的建议

1. **用专门的小号跑 bot，绝不用主号**——主号被冻结得不偿失
2. **新号先养 3-7 天**：每天正常登录、浏览动态、和好友发几条普通消息，模拟真人轨迹
3. **单实例登录**：别同时开多个协议端登同一个号
4. **控制频率**：消息间隔 ≥ 2 秒，本项目自带 429 阶梯退避 + 静默重试，别关掉
5. **别随手升级** QQ / 协议端版本，新版本常导致频繁掉线，稳定就好
6. **被踢了别慌**：重新扫码登录即可；频繁被踢就"养"几天再上
7. **白名单群**：只让 bot 待在你自己的群里，减少异常行为特征

> 一句话结论：小号 + 单实例 + 低频 + 白名单群，实际风险是"偶尔被踢、重新扫码"，而不是"立刻封号"。使用本项目的一切风险由使用者自行承担。

## 部署

### 0. 交给 AI agent 部署（推荐）

把仓库地址发给你的 AI 编程助手（Qoder / Cursor / Claude Code 等任意一个），直接说：

> 读一遍 https://github.com/m62m61m80bpzm2/glmate 的 README，帮我部署。按「前置条件」逐项检查，缺的先问我；然后安装依赖、由 `config.example.json` 生成 `config.json` 并逐项填写、启动 SnowLuma 和机器人，最后按「验证」一节确认四类消息都能回。

agent 部署前先记住三条硬信息，避免白跑一趟：

- 依赖清单在 **`zglmbot/requirements.txt`**（根目录那份只是 `-r` 转发），只有 `websockets` 和 `ddgs` 两个包
- 启动入口是 **`zglmbot/zglmbot.py`**；配置文件、日志、会话都按脚本自身位置解析（`BASE_DIR = Path(__file__).resolve().parent`），在仓库根目录直接 `python zglmbot/zglmbot.py` 就行，不用先 cd
- 配置文件是 **`zglmbot/config.json`**，仓库里不带，需要从 `config.example.json` 复制一份再填
- **SnowLuma 只能跑一个实例**，起了两个不会报错，只会让 WebUI 端口漂到 `5100` 且 OneBot 不监听；无窗口启动和按命令行重启的办法见「6. 后台运行、重启与停止」

排错时直接查文末的「故障排查」，里面按装依赖 / SnowLuma / 连接 / 使用四个阶段列了每一步可能撞到的现象、原因和命令。

agent 能独立完成的部分：装依赖、生成并填写配置、定位 SnowLuma 的 WS token、启动、读日志排错。
整条链路里真正需要人手的只有一处：**在这台电脑上把那个 QQ 小号登录到 QQ 桌面客户端**（SnowLuma 是注入这个进程来收发消息的，见「3. 启动 SnowLuma 并登录 QQ」）。另外首次进 WebUI 要人输一次密码，但密码本身 agent 能从 `SnowLuma/logs/startup-stdout.log` 里读出来告诉用户。剩下的启动、进程注入、启用 OneBot、同步 token、改配置、重启、排错，全部可以由 agent 执行完。

跑起来之后如果要改行为（换端点、加命令、改回复风格），同样把需求加上 `zglmbot/state/zglmbot.log` 的报错内容丢回给 agent 即可——核心逻辑集中在一个约 1200 行的 `zglmbot.py`，完整读进上下文没有压力。

### 1. 安装依赖

```bash
# 在仓库根目录执行；清单在 zglmbot/ 子目录里，根目录没有 requirements.txt（除转发文件外）
pip install -r zglmbot/requirements.txt

# 自检：确认装进了待会儿用来启动的那个解释器
python -c "import websockets, ddgs; print('deps OK')"
```

### 2. 配置

```bash
# 均在仓库根目录执行
cp zglmbot/config.example.json zglmbot/config.json        # bash / macOS / Linux
copy zglmbot\config.example.json zglmbot\config.json      # Windows cmd
```

编辑 `zglmbot/config.json`，按下面表格填写：

| 字段 | 含义 | 去哪填 |
|---|---|---|
| `botQQ` | 机器人的 QQ 号（即你登录并注入的那个号） | 自己 |
| `apiKey` | API Key（默认智谱，换端点时换成对应服务的 key） | [bigmodel.cn](https://bigmodel.cn) 控制台 |
| `adminQQs` | 管理员列表（默认填 botQQ 即可） | 同上 |
| `snowluma.accessToken` | **WS:3001 的 token**，注意和 HTTP:3000 不同 | 见下方查询方法 |
| `allow.groups` | 要接入的群号列表（如 `[123456789]`） | QQ 群设置 → 群号 |
| `baseUrl` | API 端点，默认智谱；任意 OpenAI 兼容服务都能填 | 见「换非智谱端点」 |
| `model` | 主模型，只留它也能跑 | — |
| `backupModel` | 备用模型，限流第 5 次起切换；**留空即只用主模型** | — |

**⚠️ 常见坑：** SnowLuma 的 `config/onebot_<QQ号>.json` 里有两个 `accessToken`，都在 `networks` 下面：
- `networks.httpServers[0].accessToken` → HTTP :3000 接口用
- `networks.wsServers[0].accessToken` → **WS :3001 接口用（填这个！）**

两者经常不一样，填错了机器人连不上。取值的快捷方式（在仓库根目录执行）：

```bash
python -c "import json;print(json.load(open('SnowLuma/config/onebot_<QQ号>.json',encoding='utf-8'))['networks']['wsServers'][0]['accessToken'])"
```

这个文件**只在 WebUI 里启用 OneBot 服务器之后才生成**。全新解压的 SnowLuma，`config/` 下只有 `runtime.json`、`webui.json` 等，没有 `onebot_*.json`——找不到它说明 OneBot 还没开、QQ 还没登录，不是路径写错了。

### 3. 启动 SnowLuma 并登录 QQ

这一节里只有「登录 QQ 桌面客户端」那一步必须人手，其余命令 agent 都可以自己执行。

```bash
cd SnowLuma
.\launcher.bat          # 或者直接在仓库根目录跑 .\start-all.bat（隐藏窗口启动）
```

启动后本机管理页在 `http://127.0.0.1:5099`（端口可在 `SnowLuma/config/runtime.json` 的 `webuiPort` 改）。按顺序做：

1. **找初始账号密码。** WebUI 首次启动会随机生成一组，只在日志里打印一次：

   ```bash
   grep "initial credentials" SnowLuma/logs/startup-stdout.log     # bash
   findstr "initial credentials" SnowLuma\logs\startup-stdout.log  # Windows cmd
   # [WebUI] initial credentials: user=admin password=<随机十六进制串>
   ```

   用户名固定是 `admin`。⚠️ **如果第一次登录后没有改密码，下次重启会重新生成一组，旧密码立刻失效**，此时重新执行上面的 grep 拿新值。改过密码之后就不会再变了，日志里那条也只是初始值。

2. **登录并改密码。** `webui.json` 里 `mustChangePassword` 为 `true` 时，进去会强制要求设置新密码，改掉它。

3. **同意 EULA**（`SnowLuma/EULA.md`，界面上勾选确认，之后会生成 `config/consent.json`）。

4. **先登录 QQ 桌面客户端。** 在这台电脑上，用要接入 AI 的那个小号把 QQ 桌面版登录好并保持在线——⚠️ **顺序很重要：QQ 先登录，再让 SnowLuma 注入**。SnowLuma 是靠注入这个已登录进程来收发消息的，QQ 没在线它就没有会话可用。

5. **进程注入。** 在 WebUI 里对 QQ 进程执行注入（一般会自动发现已登录的 `QQ.exe`）。成功后日志里会出现这三行：

   ```
   [Hook]  login detected: PID=22752 UIN=<你的小号>
   [Bridge] session started: UIN=<你的小号>
   [OneBot] network startup applied: UIN=<你的小号> adapters=2
   ```

   看到 `login detected` 才算接管成功；紧接着 `[Bridge] groups loaded: count=N` 会列出这个小号所在的群，群号对不上的话回去检查 `allow.groups`。QQ 后来被踢下线或重启过，注入会话就失效，重新注入一次即可。

6. **打开 OneBot v11 WebSocket。** WebUI 里启用 OneBot 接口，确认：

   | 项 | 值 |
   |---|---|
   | 端口 | `3001`（WS 服务器） |
   | accessToken | 复制出来填进 `zglmbot/config.json` 的 `snowluma.accessToken` |
   | reportSelfMessage | **保持开启（`true`）**，关掉之后管理员自己 @ 机器人不会触发回复，单人无法自测 |

   SnowLuma 出厂默认写的是 `false`，部署时必须改。运行中的 WS 服务读的是启动时的快照，**改完要重启 SnowLuma 才生效**——这一步 agent 可以自己做完，不需要人工点界面（重启不影响 QQ 客户端的登录态，SnowLuma 会重新注入）：

   ```powershell
   # 在 SnowLuma 目录下执行：停掉 index.mjs 进程，再隐藏启动
   Get-CimInstance Win32_Process -Filter "Name='node.exe'" |
     Where-Object { $_.CommandLine -match 'index\.mjs' } |
     ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
   Start-Process -FilePath ".\node.exe" -ArgumentList "index.mjs" `
     -WorkingDirectory (Get-Location) -WindowStyle Hidden
   ```

   只想改配置不想开浏览器，可以直接改文件（改完同样要按上面重启）：

   ```bash
   python -c "import json;p='SnowLuma/config/onebot_<QQ号>.json';d=json.load(open(p,encoding='utf-8'));d['networks']['wsServers'][0]['reportSelfMessage']=True;json.dump(d,open(p,'w',encoding='utf-8'),ensure_ascii=False,indent=2)"
   ```

   重启后 `:3001` 会短暂中断，机器人靠 5 秒重连自动接上，**不用重启机器人**。

   连接方向是：SnowLuma 作为 WS 服务端监听 `:3001`，glmate 作为客户端主动连过去（`config.json` 里的 `snowluma.wsUrl`）。所以要先起 SnowLuma 再起机器人；顺序反了也不要紧，机器人每 5 秒重连一次，SnowLuma 就绪后会自动接上。⚠️ 但 WS 地址和 token 只在机器人启动时读取一次，**改了 token 必须重启机器人**。

7. **确认接口真的开了。** QQ 未登录 / 未注入时 `:3001` 不会监听，这是最常见的"连不上"原因：

   ```bash
   netstat -ano | findstr ":3001"      # 有 LISTENING 才算就绪
   ```

   然后看 `zglmbot/state/zglmbot.log`，出现 `已连接 SnowLuma OneBot WS (bot=... enabled=True)` 即成功。两种失败现象分别对应不同的原因：

   | 日志 | 原因 |
   |---|---|
   | `ConnectionRefusedError ... 5秒后重连` | `:3001` 没监听：SnowLuma 没起、QQ 没登录或没注入 |
   | `HTTP 401` / `rejected unauthorized WebSocket upgrade` | 端口通了但 token 不对，见下一段 |

> **token 对不上（401）**：重新解压或重装 SnowLuma 之后，`onebot_<QQ号>.json` 里的 accessToken 会重新生成一份，和机器人 `config.json` 里存的旧值不再一致。用上面「配置」一节那条命令重新取值、写进 `snowluma.accessToken`、重启机器人即可。
>
> **被踢下线了怎么办**：QQ 掉线后注入会话失效，机器人日志重新出现 `ConnectionRefusedError`。在 WebUI 里重新登录 QQ 并注入即可，机器人不用重启。

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

### 6. 后台运行、重启与停止

三种启动方式，区别只在于要不要那个黑色控制台窗口：

| 方式 | 窗口 | 适用 |
|---|---|---|
| `.\start-all.bat` | SnowLuma 隐藏 + 机器人一个控制台 | 日常使用 |
| `SnowLuma\launcher.bat` | 有黑窗口（先自检 node 版本，结尾 `pause`，报错会停住给你看） | 排错、想看实时输出 |
| `zglmbot\run.bat` | 只有机器人控制台 | SnowLuma 已经开着时 |

**完全后台启动**（agent 部署时用这个，不需要人盯着窗口）：

```powershell
Start-Process -FilePath "D:\path\to\qbot\SnowLuma\node.exe" -ArgumentList "index.mjs" `
  -WorkingDirectory "D:\path\to\qbot\SnowLuma" -WindowStyle Hidden
```

**停止 / 重启**：隐藏模式没有窗口可关，按命令行特征找进程，别按进程名一刀切：

```powershell
# SnowLuma（父进程 + 服务进程都带 index.mjs）
Get-CimInstance Win32_Process -Filter "Name='node.exe'" |
  Where-Object { $_.CommandLine -match 'index\.mjs' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

# 机器人
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -match 'zglmbot\.py' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

⚠️ 用 `taskkill /IM node.exe` 会连带杀掉机器上其它 Node 程序；在 Git Bash / sh 里套 `powershell -Command "..."` 时，`"Name='node.exe'"` 的单引号会被 shell 吃掉，报 `Get-CimInstance : 无效查询`——把脚本落成 `.ps1` 文件再用 `-File` 执行最稳。

**改了东西要不要重启：**

| 改动 | 是否要重启 |
|---|---|
| `zglmbot/config.json` 的 `model` / `systemPrompt` / `allow.groups` 等 | **不用**，`load_config()` 每条消息都重读文件 |
| `zglmbot/config.json` 的 `snowluma.wsUrl` / `accessToken` | **要重启机器人**，WS 连接参数只在启动时读一次 |
| SnowLuma 的 OneBot 配置（token、`reportSelfMessage`、端口） | **要重启 SnowLuma**，运行中的服务用的是启动时的快照 |
| `zglmbot/zglmbot.py` 代码 | **要重启机器人** |

⚠️ **必须保持单实例。** SnowLuma 重复启动不会报错退出，而是把 WebUI 端口往后漂（`5099 → 5100`），OneBot 的 `3000/3001` 因为被占用而**不监听**——症状是「管理页能打开、一切正常，但机器人一直 `ConnectionRefusedError`」。检查：

```powershell
netstat -ano | findstr ":3001"    # 只应有一个 LISTENING，且 PID 属于正在服务的那个 node 进程
```

QQ 协议端同样要求单实例登录同一个号，理由见「账号安全」。

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

## 换非智谱端点

改 `baseUrl` + `apiKey` + `model` 三个字段就能换服务商。各能力的兼容性取决于所走的路径：

| 能力 | 请求路径 | 非智谱端点 |
|---|---|---|
| 对话 | `{baseUrl}/chat/completions` | ✅ 任意 OpenAI 兼容服务 |
| 读图 OCR | `{baseUrl}/chat/completions`（带 image_url） | ✅ 任意视觉模型 |
| 联网搜索 | 本地 ddgs 抓取 | ✅ 与端点无关 |
| 画图 | `{baseUrl}/images/generations`，需返回 `data[0].url` | ⚠️ 看服务是否兼容智谱风格（OpenAI 官方默认返回 base64，不可用） |
| 生成视频 | `{baseUrl}/videos/generations` + `/async-result/{task_id}` | ❌ 智谱专有异步任务协议 |
| token 计数 | `{baseUrl}/tokenizer` | ⚠️ 404 时自动降级为估算，不影响运行 |

**纯对话端点示例**（DeepSeek）：

```json
{
  "baseUrl": "https://api.deepseek.com/v1",
  "apiKey": "sk-xxxx",
  "model": "deepseek-chat",
  "backupModel": "",
  "multimodalModel": "",
  "imageGenModel": "",
  "videoGenModel": "",
  "contextWindows": { "deepseek-chat": 64000 }
}
```

配置规则：

- **只留一个主模型**：`backupModel` 置为 `""`（或填成和 `model` 相同的值）。此时限流重试全程只打主模型，也不会误发「切换到备用模型」提示。
- **关掉某项能力**：把对应的模型字段置为 `""`。程序会把它从工具列表里摘除，并在 system prompt 里追加「当前未开启的能力」说明，模型不会假装自己画了图。`webSearch.enabled: false` 同理。
- **补窗口**：`contextWindows` 里查不到的模型按 128000 处理，非智谱模型建议显式写进配置，否则压缩阈值会算错。
- **改人设**：`systemPrompt` 里默认写的是「可以画图、生成视频」，端点不提供这些能力时记得删掉那半句。

`multimodalModel` 若指向的模型和主模型是同一个（很多新版视觉模型本身就能图文混输），把它填成 `model` 的值即可，读图会走同一个端点。

## 故障排查（部署会踩的坑，按阶段列全）

日志位置：机器人 `zglmbot/state/zglmbot.log`，协议端 `SnowLuma/logs/`。

### 装依赖阶段

| 现象 | 原因 | 处理 |
|---|---|---|
| 根目录找不到 `requirements.txt` | 真实清单在 `zglmbot/requirements.txt`，根目录那份只是 `-r` 转发 | `pip install -r zglmbot/requirements.txt` |
| `ModuleNotFoundError: No module named 'websockets'`，但 `pip list` 里明明有 | 机器上有多个 Python，依赖装进了另一个解释器；`.bat` 先找 PATH 的 `python`，再退 `py -3` | 用启动它的那个解释器装：`<那个python> -m pip install -r zglmbot\requirements.txt` |
| 启动就打印「缺少依赖 websockets」后退出 | 同上，这是预期的快速失败，没有栈 | 同上 |

### SnowLuma 阶段

| 现象 | 原因 | 处理 |
|---|---|---|
| 目录里只有 `node.exe` 和 `native/`，没有 `index.mjs` | 解压不完整；或者被 `git clean -fdX` 删过（`SnowLuma/` 在 .gitignore 里） | 重新下载完整压缩包解压进 `SnowLuma/`，`data/<QQ号>/` 还在就不用重新登录注入 |
| 找不到 `config/onebot_<QQ号>.json` | 不是路径写错——这个文件在 WebUI 里**启用 OneBot 之后**才生成 | 先启用 OneBot 再回来取 token |
| WebUI 提示「密码错误」 | 初始密码是一次性的；没改过密码就重启，会重新生成一枚，旧密码作废 | `findstr "initial credentials" SnowLuma\logs\startup-stdout.log` 取最新值，登录后立刻改密码 |
| 管理页能打开、看着一切正常，但机器人连不上，WebUI 端口变成了 `5100` | **起了两个 SnowLuma 实例**：第二个抢不到 `3000/3001`，只把 WebUI 往后漂 | 按命令行匹配杀掉多余实例，只留一个（见「6. 后台运行、重启与停止」） |
| 注入后没有 `[Hook] login detected` | QQ 桌面端还没登录，或登录后才启动 SnowLuma 需要重新注入 | 先登录 QQ 客户端，再在 WebUI 里注入 |

### 连接阶段

| 日志现象 | 原因 | 处理 |
|---|---|---|
| `ConnectionRefusedError ... 5秒后重连` | `:3001` 根本没监听：SnowLuma 没起 / QQ 没登录 / 没注入 / OneBot 没启用 | `netstat -ano \| findstr ":3001"` 先确认端口 |
| `HTTP 401` / `rejected unauthorized WebSocket upgrade` | 端口通了但 token 不对。**重新解压 SnowLuma 后必现**——token 会重新生成 | 取 `networks.wsServers[0].accessToken` 写进 `snowluma.accessToken`，然后**重启机器人**（token 只在启动时读一次） |
| 文字能回、图片发不出去 | 填成了 `networks.httpServers[0].accessToken`（:3000 那枚） | 换成 `wsServers[0]` 的 |
| 改了 token 但机器人还是 401 | 只改了配置没重启 | 重启机器人 |
| 改了 `model` / `systemPrompt` 不生效 | 一般不会——`load_config()` 每条消息重读；若确实不生效，多半是 JSON 语法错误导致回落默认配置 | 看日志里有没有 `配置读取失败` |

### 使用阶段

| 现象 | 原因 | 处理 |
|---|---|---|
| 群里发消息完全不回 | `requireMention: true` 且没 @ 机器人；或群号不在 `allow.groups` | 日志里会记 `收到群消息 ... ats=[]`，说明收到了但没触发，属正常 |
| 自己 @ 机器人没反应 | SnowLuma `reportSelfMessage` 是 `false`（出厂默认），或改了没重启 SnowLuma | 置 `true` 并重启 SnowLuma；生效时日志会出现 `self=True` 的事件 |
| 开了 `reportSelfMessage` 后怕自己回复自己 | 内置回声去重（20 秒窗口）在拦，日志 `自己消息未触发 ... echo=True` | 正常，不要关 |
| 群里报 `[429]` | 免费额度限流，十档阶梯重试（约 80 秒）耗尽才会说一次 | 等几分钟；别关掉重试 |
| 换非智谱端点后画图/视频报 404 或「工具执行失败」 | 这两个是智谱专有接口 | 把 `imageGenModel` / `videoGenModel` 置空关闭 |
| 日志出现「当前配置没有启用这个能力」 | 模型调用了被摘除的工具，已拦截 | 正常保护，不用管 |
| 长对话压缩时机不对 | `contextWindows` 里没有这个模型，按 128000 兜底 | 在 `contextWindows` 里补上真实窗口 |

## 开源协议

MIT — 详见 `LICENSE`。
本软件仅供学习研究，使用风险由用户自行承担。QQ 机器人行为受腾讯政策约束，请遵守相关规范。
