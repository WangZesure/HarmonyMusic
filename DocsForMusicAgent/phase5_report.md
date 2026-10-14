# 阶段 5 报告：接入 App（Koa + 鸿蒙）

> 2026-10-13。承接阶段 3/4。目标：Koa 新增 `/agent/chat`（SSE 流式 + 复用 JWT），鸿蒙端新增“AI 搜歌”对话页。

## 1. 实现

**Koa 后端**（`openKoa/`）
- `routes/index.js`：`router.post('/agent/chat', verifyToken(), controller.agentChat)` —— 鉴权复用现有 JWT 中间件。
- `controller/api.js · agentChat`：把请求转发给 Python Agent 服务（`AGENT_URL`，默认 `http://127.0.0.1:8000`），用 **SSE** 返回过程：
  - `event: status` `{stage:"understanding"|"searching", text}`（正在理解→正在检索）
  - `event: result` `{songs, message, relaxed, trace}`
  - `event: error` `{message}`
  - 用户身份经 `X-User-Id` 头透传给 Agent（长期偏好用）。
  - **说明（更正）**：两条 `status` 是在**调用 Agent 之前固定发出**的，`result` 一次性返回——不是 Agent 真的分阶段推送。要真流式，需让 Agent 分阶段推（二选一，见 `phase3loop` F-loop）。

**鸿蒙客户端**（`myMusic/`）
- 新增 `pages/AiSearch.ets`：对话式「AI 搜歌」页。
  - 用 `@ohos.net.http` 的 `requestInStream` 消费 SSE（`on('dataReceive')` 解析 `event:`/`data:`）。
  - 结果显示为歌曲卡片（封面取自 `Config.resolveUrl`，歌名/歌手/推荐理由）。
  - **回查补全**：按契约 C7 工具只给 `song_id`，客户端用 `getSongDetail` 回查库拿到 `pic/url`。
  - 点击卡片 → `AvPlayerUtils.playSingle(song)` 播放（复用现有播放器）。
- `resources/base/profile/main_pages.json` 注册 `pages/AiSearch`；`pages/Index.ets` 加「AI 搜歌」入口按钮。
- 音视频/图片地址统一走阶段 1.6 的 `ConfigUtil.resolveUrl()`。

## 2. 实测（本机）

启动 `uvicorn agent.app:app --port 8000` 与 Koa（:3000）后：

```
POST /login {user1,123456}              -> 200, token
POST /agent/chat (Authorization: token)  -> SSE:
  event: status {"正在理解你的需求…"}
  event: status {"正在检索曲库…"}
  event: result {"songs":[{song_id:1969615,name:"…",moods:["希望","温暖","燃"],…}, …]}
第二轮 (同 sessionId) "换一首"            -> 返回不同歌曲（已排除上一轮）
```

- JWT 鉴权生效（无 token → 801/802）。
- 多轮会话排除生效（第二轮回不同歌）。

## 3. 局限

- **客户端未在本环境编译/真机验证**（需 DevEco Studio + 鸿蒙设备）；`AiSearch.ets` 为按现有模式编写，需在 DevEco 里过一遍编译。
- SSE 在 ArkTS 侧用 `requestInStream`（该 API 在新版本标注 deprecated），如需更稳妥的流式可换 `http.HttpRequest` 的事件式接口。
- 语音输入**未接**（工作台有 `AudioCapturer`/`AsrConstants`，输入框可后续接上）。
- Agent 服务需另起进程（uvicorn :8000）；生产建议由 Koa/PM2 托管并加超时/重试。
- **Agent 服务（:8000）本身无鉴权**，只假定跑在 loopback；`/tool/{name}`、`/agent/search` 亦无鉴权。客户端统一走带 JWT 的 Koa（`/agent/chat`）。
- Koa→Agent 的转发用 `fetch`，**已加 20s 超时**（`AbortSignal.timeout(20000)`）；仍无重试。
- AI 页**假设播放器已初始化**（`Index.ets` 里 `AvPlayerUtils.init()`）；冷启动直达该页时播放可能不可用。
- 入口是 `Index.ets` 上的**浮动按钮（坐标写死）**，非独立 Tab。
- SSE 消费是**手写按 `\n\n` 分块 + 缓冲**，未穷尽极端分块边界。

### 更正：关于 `reason`（此前报告写错了）

早前一次 curl 测试里 `result.songs[].reason` 为空，当时记为"待查"。**复测确认那是测试假象**：
Windows 控制台把中文按 **GBK** 发出，Agent 端按 UTF-8 解码失败 → 查询变乱码 → 结果与理由都不对。
改用 UTF-8 请求体（`--data-binary @file`）后，agent 直连与 Koa SSE **都正常返回中文 `reason`**，且命中正确（同一批深夜伤感歌）。
客户端（`AiSearch.ets`）本就发 UTF-8，不受影响。**结论：不是产品缺陷。**

**补记（F4）**：`reason` 为空还有**第二个、真实的代码原因**——`respond.py` 对 `Match` 对象误用 `.get`（对象不是 dict）抛异常，被 `except Exception: pass` 吞掉，**歌词类查询必现**。已在 F-loop 修复（改 `getattr`、错误记入 trace）。


## 4. 交付物

`openKoa/routes/index.js`、`openKoa/controller/api.js`(`agentChat`)、`myMusic/entry/src/main/ets/pages/AiSearch.ets`、
`myMusic/entry/src/main/resources/base/profile/main_pages.json`、`myMusic/entry/src/main/ets/pages/Index.ets`（入口）。

复现：`uvicorn agent.app:app --port 8000` + `node openKoa/bin/www`，客户端装到设备/模拟器后从首页点「AI 搜歌」。
