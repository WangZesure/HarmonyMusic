"""Build a HarmonyOS debug provisioning profile from the SDK template.

    python make_profile.py <udid> [<udid> ...]

Writes profile.json (unsigned). Sign it afterwards with hap-sign-tool sign-profile.

NOTE ON ENCODING: `development-certificate` must be the cert's DER as **URL-safe**
base64 (no '+' or '/'), NOT PEM and NOT standard base64. hap-sign-tool base64-decodes
this field with a decoder that rejects '+' and '/' — a PEM (which has spaces in its
-----BEGIN/END----- markers) fails with "Illegal base64 character 20", and standard
base64 fails with "Illegal base64 character 2b". Verified empirically 2026-10-14.
"""
import base64
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).parent
TEMPLATE = pathlib.Path(
    r"D:\apps\tools\arkstudio\DevEco Studio\sdk\default\openharmony\toolchains"
    r"\lib\UnsgnedDebugProfileTemplate.json"
)
BUNDLE = "com.example.mymusic01"

# The shipped template's validity is 2021-02-02 .. 2024-01-13 (already expired).
NOT_BEFORE = 1735689600   # 2025-01-01
NOT_AFTER = 2082758400    # 2036-01-01


def leaf_cert_b64url(cer_path: pathlib.Path) -> str:
    """First certificate in the PEM chain -> DER -> URL-safe base64."""
    pem = cer_path.read_text(encoding="utf-8")
    block = re.findall(
        r"-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----", pem, re.S
    )[0]
    der = base64.b64decode("".join(block.split()))
    return base64.urlsafe_b64encode(der).decode("ascii")


def main() -> int:
    udids = sys.argv[1:]
    if not udids:
        print("usage: make_profile.py <udid> [<udid> ...]", file=sys.stderr)
        return 2

    prof = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    prof["validity"] = {"not-before": NOT_BEFORE, "not-after": NOT_AFTER}
    prof["bundle-info"]["bundle-name"] = BUNDLE
    prof["bundle-info"]["development-certificate"] = leaf_cert_b64url(HERE / "mymusic.cer")
    prof["debug-info"]["device-ids"] = udids
    prof["debug-info"]["device-id-type"] = "udid"

    out = HERE / "profile.json"
    out.write_text(json.dumps(prof, indent=4, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {out} (bundle={BUNDLE}, devices={len(udids)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

