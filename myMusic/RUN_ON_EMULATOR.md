# 在 DevEco / 模拟器上跑通 myMusic（本机实操记录，2026-10-14）

本机环境：Windows 11，DevEco Studio `D:\apps\tools\arkstudio\DevEco Studio`（内置 SDK **API 26**），
模拟器 Huawei_Phone（**HarmonyOS 5.0.5(17)**，镜像与实例都在 **D 盘**，因为 C 盘只剩 1GB）。

---

## 1. 为什么 DevEco 里「工程同步失败」

`build-profile.json5` 里有一行 **空字符串的 `targetSdkVersion: ""`**。schema
（`tools/hvigor/hvigor-ohos-plugin/res/schemas/ohos-project-build-profile-schema_zh.json`）
规定该字段缺省即等于 `compileSdkVersion`，空串违反 pattern → 同步失败。
**已删除该行**（`compatibleSdkVersion: "5.0.2(14)"` 是「最低兼容 API」，本机 API 26 的
SDK 可以正常编译，14 ≤ 17 模拟器也能装，不必改）。

## 2. 编译错误（新编译器更严格）

本机 SDK 是 API 26，比作者当年用的 5.0.2 严格，暴露了 3 处真实问题，**已修**：

| 文件 | 问题 |
|---|---|
| `pages/common/ConfigUtil.ets` | `resolveUrl(u: string)`，但调用方传的是可选字段 `pic?`/`avatar?` → 放宽为 `string \| undefined`，返回 `''` |
| `pages/SongsDetail.ets:44` | `Config.resolveUrl(this.songDetail)[0].pic` 把**数组**传给了只收字符串的函数 → 应为 `resolveUrl(this.songDetail[0].pic)` |
| `pages/tabs/Mine.ets` | 「登录/注册」跳 `pages/pass/CodeLoginStepOne`——**该页面不存在也不在白名单**，按钮点了没反应 → 改 `pages/Login` |

## 3. 模拟器：镜像要自己下，且别放 C 盘

本机从没下过系统镜像（`.temp` 里只有 1.4MB 残包），启动报 `can not read uuid file`。
另外 C 盘仅剩 1GB，实例放 C 盘必失败（历史上就报过 `NO_ENOUGH_BASEDISK_SPACE_ERR`）。

```bash
EMU="/d/apps/tools/arkstudio/DevEco Studio/tools/emulator/Emulator.exe"
"$EMU" -license accept
"$EMU" -install -deviceType phone -osVersion "HarmonyOS 5.0.5(17)" -imageRoot "D:\HarmonyEmu\image"   # 约 2.0GB
"$EMU" -create MyMusicPhone -deviceType phone -osVersion "HarmonyOS 5.0.5(17)" \
       -instancePath "D:\HarmonyEmu\instance" -imageRoot "D:\HarmonyEmu\image"
```

**启动时必须在 stdin 上回一个 `y`**（有个许可确认，无 TTY 时默认 N 会退出）：

```bash
printf 'y\n' | "$EMU" -start MyMusicPhone -instancePath "D:\HarmonyEmu\instance" -imageRoot "D:\HarmonyEmu\image"
```

## 4. 签名（hvigor 走不通，用 SDK 自带的 hap-sign-tool）

DevEco 的签名密码是**本机 material 加密串**（`decipher-util` 读 `~/.ohos/config/material`，
要求 ≥32 字符），命令行复现不了；原配置还指向另一台机器的 macOS 证书路径。
所以 `build-profile.json5` 的 `signingConfigs` 置空，改由脚本本地签名
（成品脚本与中间件在 `C:\Users\Lenovo\.ohos\config\mymusic_debug\`）。

关键坑（都踩过）：

1. profile 的 `development-certificate` 必须是证书 DER 的 **URL-safe base64**
   （标准 base64 里的 `+` 会被拒：`Illegal base64 character 2b`；PEM 里的空格报 `20`）。
2. 模板自带的 `validity` 是 2021–2024，**已过期**，要改到未来。
3. `sign-app` 必须带 **`-inForm zip`**，否则报 `Please check if file is zip64 format`。
4. profile 的 `device-ids` 要填**真实 UDID**（`hdc shell bm get -u`），
   且要跟 `hdc list targets` 里的目标区分开——后者是 `127.0.0.1:5555`，不是 UDID。

## 5. 跑起来

```bash
# 后端（宿主机）
cd openKoa && node bin/www                                  # :3000
D:\apps\tools\Conda\python.exe -m uvicorn agent.app:app --port 8000   # :8000

# 构建 / 签名 / 安装 / 启动（一键，脚本随仓库走）
bash myMusic/scripts/sign_and_install_emulator.sh --install

# 可用环境变量覆盖默认值：
#   DEVECO_HOME  DevEco Studio 安装目录（默认 D:/apps/tools/arkstudio/DevEco Studio）
#   SIGN_WORK    签名材料目录（默认 ~/.ohos/config/mymusic_debug）
#   PYTHON       生成 profile 用的解释器

# 关键：App 里写的是 localhost:3000，模拟器里的 localhost 不是宿主机，
# 用反向端口转发打通（fport 是 host→device，方向反了，要用 rport）
HDC="/d/apps/tools/arkstudio/DevEco Studio/sdk/default/openharmony/toolchains/hdc.exe"
"$HDC" rport tcp:3000 tcp:3000
```

## 6. 已知问题（本次发现，未改）

- **`verifyToken()` 鉴权失败返回 HTTP 200**（`utils/token.js:24,28` 只设 `ctx.body` 不设 status）。
  于是 App 未登录时 `/agent/chat` 收到 200 + `{"code":802,...}`（39 字节），
  SSE 解析器读不到 `data:` 事件 → **AI 搜歌页什么都不显示**，没有任何提示。
  两个可改点：Koa 返回 401；AiSearch 在拿不到 SSE 事件时提示「请先登录」。
- **AI 搜歌必须先登录**（`/agent/chat` 挂了 `verifyToken()`）。首页/推荐不需要登录，
  所以容易误以为整个 App 都不用登录。
- 首页「AI 推荐单曲」实际走的是**降级**（`recommend/models/` 没训练，Python 脚本退出 1，
  Koa 回落到 `ORDER BY id LIMIT 6`）——这是文档里写过的既有降级，不是本次引入。
- 客户端 `ConfigUtil.SERVER` 仍是 `http://localhost:3000`；换真机要改成宿主机局域网 IP。
