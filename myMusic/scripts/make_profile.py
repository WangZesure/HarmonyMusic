"""Build a HarmonyOS debug provisioning profile from the SDK template.

    python make_profile.py <udid> [<udid> ...] [--work <dir>]

`--work` is the directory holding the signing materials (mymusic.cer) and where
profile.json is written. It defaults to $SIGN_WORK, else
~/.ohos/config/mymusic_debug.

The SDK template is read from $DEVECO_HOME/sdk/default/openharmony/toolchains/lib/
(DEVECO_HOME defaults to the DevEco install on this machine).

Sign the result afterwards with hap-sign-tool sign-profile.

NOTE ON ENCODING: `development-certificate` must be the cert's DER as **URL-safe**
base64 (no '+' or '/'), NOT PEM and NOT standard base64. hap-sign-tool base64-decodes
this field with a decoder that rejects '+' and '/' — a PEM (which has spaces in its
-----BEGIN/END----- markers) fails with "Illegal base64 character 20", and standard
base64 fails with "Illegal base64 character 2b". Verified empirically 2026-10-14.
"""
import base64
import json
import os
import pathlib
import re
import sys

DEFAULT_DEVECO_HOME = r"D:\apps\tools\arkstudio\DevEco Studio"
TEMPLATE_REL = pathlib.Path("sdk/default/openharmony/toolchains/lib/UnsgnedDebugProfileTemplate.json")
BUNDLE = "com.example.mymusic01"

# The shipped template's validity is 2021-02-02 .. 2024-01-13 (already expired).
NOT_BEFORE = 1735689600   # 2025-01-01
NOT_AFTER = 2082758400    # 2036-01-01


def template_path() -> pathlib.Path:
    home = os.environ.get("DEVECO_HOME") or DEFAULT_DEVECO_HOME
    return pathlib.Path(home) / TEMPLATE_REL


def default_work() -> pathlib.Path:
    env = os.environ.get("SIGN_WORK")
    if env:
        return pathlib.Path(env)
    return pathlib.Path.home() / ".ohos" / "config" / "mymusic_debug"


def leaf_cert_b64url(cer_path: pathlib.Path) -> str:
    """First certificate in the PEM chain -> DER -> URL-safe base64."""
    pem = cer_path.read_text(encoding="utf-8")
    block = re.findall(
        r"-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----", pem, re.S
    )[0]
    der = base64.b64decode("".join(block.split()))
    return base64.urlsafe_b64encode(der).decode("ascii")


def main() -> int:
    argv = sys.argv[1:]
    work = default_work()
    if "--work" in argv:
        i = argv.index("--work")
        if i + 1 >= len(argv):
            print("--work needs a directory", file=sys.stderr)
            return 2
        work = pathlib.Path(argv[i + 1])
        del argv[i:i + 2]

    udids = argv
    if not udids:
        print("usage: make_profile.py <udid> [<udid> ...] [--work <dir>]", file=sys.stderr)
        return 2

    tpl = template_path()
    if not tpl.exists():
        print(f"找不到 SDK 模板: {tpl}\n请设置 DEVECO_HOME 指向 DevEco Studio 安装目录", file=sys.stderr)
        return 2
    cer = work / "mymusic.cer"
    if not cer.exists():
        print(f"找不到签名证书: {cer}\n请先生成签名材料，或用 --work 指定目录", file=sys.stderr)
        return 2

    prof = json.loads(tpl.read_text(encoding="utf-8"))
    prof["validity"] = {"not-before": NOT_BEFORE, "not-after": NOT_AFTER}
    prof["bundle-info"]["bundle-name"] = BUNDLE
    prof["bundle-info"]["development-certificate"] = leaf_cert_b64url(cer)
    prof["debug-info"]["device-ids"] = udids
    prof["debug-info"]["device-id-type"] = "udid"

    out = work / "profile.json"
    out.write_text(json.dumps(prof, indent=4, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {out} (bundle={BUNDLE}, devices={len(udids)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
