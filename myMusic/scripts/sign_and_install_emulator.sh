#!/usr/bin/env bash
# Build -> sign -> (optionally) install the myMusic HAP onto a HarmonyOS emulator.
#
#   ./sign_and_install_emulator.sh              # build + sign only
#   ./sign_and_install_emulator.sh --install    # ... then hdc install + launch
#
# Why this exists: hvigor's own signing wants DevEco-encrypted passwords (the
# "Automatically generate signature" GUI path), so build-profile.json5 ships with an
# empty signingConfigs and we sign the unsigned HAP ourselves with hap-sign-tool.
#
# 可覆盖的环境变量（都有本机默认值）：
#   DEVECO_HOME  DevEco Studio 安装目录
#   SIGN_WORK    签名材料目录（mymusic.cer / mymusic.p12 / profile.json / *.p7b）
#   PYTHON       带标准库即可，用于生成 profile
#
# 签名材料怎么来（只需做一次）：
#   mkdir -p "$SIGN_WORK" && cd "$SIGN_WORK"
#   # 1. 从 SDK 的 OpenHarmony.p12 导出 rootCA / subCA
#   # 2. 生成应用密钥对 + 证书链 -> mymusic.cer
#   详见 DocsForMusicAgent/RUN_ON_EMULATOR.md 或 myMusic/RUN_ON_EMULATOR.md
set -euo pipefail
export MSYS_NO_PATHCONV=1

# 转成 C:/ 形式，bash 和 Windows 工具都能吃
to_win() { cygpath -m "$1" 2>/dev/null || echo "$1"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_DIR_WIN="$(to_win "$SCRIPT_DIR")"
PROJ="$(to_win "$(cd "$SCRIPT_DIR/.." && pwd)")"

DS="${DEVECO_HOME:-D:/apps/tools/arkstudio/DevEco Studio}"
WORK="${SIGN_WORK:-$HOME/.ohos/config/mymusic_debug}"
WORK="$(to_win "$WORK")"
PY="${PYTHON:-D:/apps/tools/Conda/python.exe}"

JAVA="$DS/jbr/bin/java.exe"
LIB="$DS/sdk/default/openharmony/toolchains/lib"
HDC="$DS/sdk/default/openharmony/toolchains/hdc.exe"
SIGN="$DS/tools/hvigor/bin/hvigorw.js"

UNSIGNED="$PROJ/entry/build/default/outputs/default/entry-default-unsigned.hap"
SIGNED="$PROJ/entry/build/default/outputs/default/entry-default-signed.hap"

# 前置检查：缺什么就直接说清楚，而不是跑到一半报一句难懂的错
for f in "$JAVA" "$HDC" "$SIGN" "$LIB/hap-sign-tool.jar"; do
  [ -e "$f" ] || { echo "找不到 $f —— 请用 DEVECO_HOME 指向 DevEco Studio 安装目录" >&2; exit 1; }
done
for f in "$WORK/mymusic.cer" "$WORK/mymusic.p12"; do
  [ -e "$f" ] || { echo "找不到签名材料 $f —— 请用 SIGN_WORK 指向签名材料目录" >&2; exit 1; }
done

echo "==> 1/4 build (hvigor, unsigned)"
cd "$PROJ"
DEVECO_SDK_HOME="$DS/sdk" PATH="$DS/tools/node:$PATH" \
  node "$SIGN" assembleHap --no-daemon

echo "==> 2/4 profile (needs the emulator UDID)"
if [ "${1:-}" = "--install" ]; then
  # NOTE: `hdc list targets` prints the connect address (127.0.0.1:5555), NOT the UDID the
  # profile needs. `bm get -u` prints two lines, the UDID is the hex string on the last one.
  UDID="$("$HDC" shell bm get -u 2>/dev/null | tr -d '\r' | grep -oE '[0-9A-Fa-f]{16,}' | tail -1)"
  [ -n "$UDID" ] || { echo "没有检测到设备，请先启动模拟器" >&2; exit 1; }
  echo "    device UDID: $UDID"
else
  UDID="${UDID:-454D55057494E058383601EDEAB1C0C13410080AEC6E5CD94625B00000000000}"
  echo "    using UDID: $UDID (加 --install 用真机真实 UDID)"
fi

# make_profile.py 随脚本一起发布，签名材料在 WORK，两者分开
"$PY" "$SCRIPT_DIR_WIN/make_profile.py" "$UDID" --work "$WORK"
"$JAVA" -jar "$LIB/hap-sign-tool.jar" sign-profile -mode localSign \
  -keyAlias "openharmony application profile debug" -keyPwd 123456 \
  -profileCertFile "$LIB/OpenHarmonyProfileDebug.pem" \
  -inFile "$WORK/profile.json" -outFile "$WORK/mymusic.p7b" -signAlg SHA384withECDSA \
  -keystoreFile "$LIB/OpenHarmony.p12" -keystorePwd 123456 >/dev/null

echo "==> 3/4 sign the HAP (-inForm zip is REQUIRED)"
"$JAVA" -jar "$LIB/hap-sign-tool.jar" sign-app -mode localSign \
  -keyAlias mymusic -keyPwd 123456 \
  -appCertFile "$WORK/mymusic.cer" -profileFile "$WORK/mymusic.p7b" \
  -inFile "$UNSIGNED" -inForm zip -outFile "$SIGNED" \
  -signAlg SHA256withECDSA -keystoreFile "$WORK/mymusic.p12" -keystorePwd 123456
echo "    -> $SIGNED"

if [ "${1:-}" = "--install" ]; then
  echo "==> 4/4 install + launch"
  # hdc 要原生 Windows 路径；"C:/..." 形式会被当成相对路径
  WIN_SIGNED="$(cygpath -w "$SIGNED" 2>/dev/null || echo "$SIGNED")"
  "$HDC" install -r "$WIN_SIGNED"
  "$HDC" shell aa start -a EntryAbility -b com.example.mymusic01
else
  echo "==> 4/4 skipped (加 --install 可推到模拟器)"
fi
