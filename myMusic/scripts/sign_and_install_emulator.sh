#!/usr/bin/env bash
# Build -> sign -> install the myMusic HAP onto the HarmonyOS emulator.
#
#   ./build_sign_install.sh            # build + sign only
#   ./build_sign_install.sh --install  # ... then hdc install + launch
#
# Why this exists: hvigor's own signing wants DevEco-encrypted passwords (the
# "Automatically generate signature" GUI path), so build-profile.json5 ships with an
# empty signingConfigs and we sign the unsigned HAP ourselves with hap-sign-tool.
set -euo pipefail
export MSYS_NO_PATHCONV=1

DS="D:/apps/tools/arkstudio/DevEco Studio"
JAVA="$DS/jbr/bin/java.exe"
LIB="$DS/sdk/default/openharmony/toolchains/lib"
HDC="$DS/sdk/default/openharmony/toolchains/hdc.exe"
SIGN="$DS/tools/hvigor/bin/hvigorw.js"

PROJ="D:/1Projects/HarmonyMusic/myMusic"
WORK="C:/Users/Lenovo/.ohos/config/mymusic_debug"
UNSIGNED="$PROJ/entry/build/default/outputs/default/entry-default-unsigned.hap"
SIGNED="$PROJ/entry/build/default/outputs/default/entry-default-signed.hap"
PY="D:/apps/tools/Conda/python.exe"

echo "==> 1/4 build (hvigor, unsigned)"
cd "$PROJ"
DEVECO_SDK_HOME="$DS/sdk" PATH="$DS/tools/node:$PATH" \
  node "$SIGN" assembleHap --no-daemon

echo "==> 2/4 profile (needs the emulator UDID)"
if [ "${1:-}" = "--install" ]; then
  # NOTE: `hdc list targets` prints the connect address (127.0.0.1:5555), NOT the UDID the
  # profile needs. The real one comes from `bm get -u`.
  UDID="$("$HDC" shell bm get -u 2>/dev/null | tr -d '\r' | sed -n 's/.*: *//p' | tail -1)"
  [ -n "$UDID" ] || { echo "no device; start the emulator first"; exit 1; }
  echo "    device UDID: $UDID"
else
  UDID="${UDID:-454D55057494E058383601EDEAB1C0C13410080AEC6E5CD94625B00000000000}"
  echo "    using UDID: $UDID (pass --install for the real one)"
fi
cd "$WORK"
"$PY" make_profile.py "$UDID"
"$JAVA" -jar "$LIB/hap-sign-tool.jar" sign-profile -mode localSign \
  -keyAlias "openharmony application profile debug" -keyPwd 123456 \
  -profileCertFile "$LIB/OpenHarmonyProfileDebug.pem" \
  -inFile profile.json -outFile mymusic.p7b -signAlg SHA384withECDSA \
  -keystoreFile "$LIB/OpenHarmony.p12" -keystorePwd 123456 >/dev/null

echo "==> 3/4 sign the HAP (-inForm zip is REQUIRED)"
"$JAVA" -jar "$LIB/hap-sign-tool.jar" sign-app -mode localSign \
  -keyAlias mymusic -keyPwd 123456 \
  -appCertFile mymusic.cer -profileFile mymusic.p7b \
  -inFile "$UNSIGNED" -inForm zip -outFile "$SIGNED" \
  -signAlg SHA256withECDSA -keystoreFile mymusic.p12 -keystorePwd 123456
echo "    -> $SIGNED"

if [ "${1:-}" = "--install" ]; then
  echo "==> 4/4 install + launch"
  # hdc wants a native Windows path here; a "D:/..." form gets resolved against the cwd.
  WIN_SIGNED="$(cygpath -w "$SIGNED" 2>/dev/null || echo "$SIGNED")"
  "$HDC" install -r "$WIN_SIGNED"
  "$HDC" shell aa start -a EntryAbility -b com.example.mymusic01
else
  echo "==> 4/4 skipped (pass --install to push to the emulator)"
fi
