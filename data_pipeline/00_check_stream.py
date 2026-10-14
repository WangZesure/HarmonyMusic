"""1.0 Feasibility gate - can the HarmonyOS AVPlayer actually play a Jamendo URL?

Run this FIRST, before committing to any storage scheme. It answers three things:
  1. Does the `/tracks/file/...&action=stream` redirect URL resolve to a playable file?
  2. How many redirects does it take, and is the final URL a stable storage host?
  3. Does a non-downloadable track really 404 there (so we know to gate selection on it)?

    python data_pipeline/00_check_stream.py

Exit code 0 only if at least one streamable track was reachable.
"""
from __future__ import annotations

import sys

import requests

import config
import common

log = common.get_logger()


def probe(url: str, label: str, nbytes: int = 65536) -> dict:
    """GET the URL as an audio client would and report what comes back."""
    result = {"label": label, "url": url}
    try:
        resp = requests.get(url, stream=True, allow_redirects=True, timeout=25,
                            headers={"User-Agent": common.USER_AGENT})
        result["status"] = resp.status_code
        result["redirects"] = [h.status_code for h in resp.history]
        result["final_url"] = resp.url
        result["content_type"] = resp.headers.get("Content-Type")
        result["content_length"] = resp.headers.get("Content-Length")
        if resp.status_code == 200:
            chunk = resp.raw.read(nbytes, decode_content=True)
            result["bytes_read"] = len(chunk or b"")
            result["magic"] = (chunk or b"")[:4].hex()
        resp.close()
    except requests.RequestException as e:
        result["error"] = f"{type(e).__name__}: {e}"
    return result


def main() -> int:
    if not config.JAMENDO_CLIENT_ID:
        log.error("JAMENDO_CLIENT_ID not set - copy .env.example to .env and fill it in.")
        return 2

    client = common.JamendoClient()
    log.info("Fetching a small sample of vocal tracks to probe...")
    raw = list(client.iter_tracks(
        target=20,
        include="musicinfo+licenses",
        audioformat=config.JAMENDO_AUDIOFORMAT,
        vocalinstrumental="vocal",
        durationbetween=f"{config.DURATION_MIN}_{config.DURATION_MAX}",
    ))
    tracks = [common.flatten_track(t) for t in raw]
    allowed = [t for t in tracks if t["audiodownload_allowed"]]
    denied = [t for t in tracks if not t["audiodownload_allowed"]]
    log.info("sample: %d tracks (%d downloadable, %d not)", len(tracks), len(allowed), len(denied))

    report = {"downloadable_allowed_count": len(allowed), "probes": []}

    for t in allowed[:3]:
        log.info("probe track %s - %s", t["id"], t["name"])
        report["probes"].append({"track_id": t["id"], "name": t["name"], "kind": "direct",
                                 "result": probe(t["audio"], "direct audio field")})
        report["probes"].append({"track_id": t["id"], "name": t["name"], "kind": "stream",
                                 "result": probe(client.stream_url(t["id"], action="stream"),
                                                 "tracks/file?action=stream")})

    if denied:
        t = denied[0]
        log.info("probe non-downloadable track %s (expect 404 from /tracks/file)", t["id"])
        report["probes"].append({"track_id": t["id"], "name": t["name"], "kind": "denied",
                                 "result": probe(client.stream_url(t["id"], action="stream"),
                                                 "tracks/file (should 404)")})

    # summarize
    ok = 0
    print("\n" + "=" * 78)
    for p in report["probes"]:
        r = p["result"]
        status = r.get("status", r.get("error", "?"))
        line = f"  [{p['kind']:6}] track {p['track_id']}: {status}"
        if r.get("redirects"):
            line += f"  redirects={r['redirects']}"
        if r.get("content_type"):
            line += f"  type={r['content_type']}"
        if r.get("bytes_read"):
            line += f"  got={r['bytes_read']}B"
            ok += 1
        print(line)
        if r.get("final_url"):
            print(f"           final: {r['final_url'][:110]}")
        if r.get("error"):
            print(f"           error: {r['error']}")
    print("=" * 78)

    common.save_json(config.REPORTS_DIR / "00_stream_check.json", report)

    if ok == 0:
        log.error("No streamable track responded with bytes. The URL scheme needs rework "
                  "(likely a Koa `/stream/:id` proxy). See phase12.md section 1.0.")
        return 1
    log.info("OK - %d probe(s) returned audio bytes. AVPlayer follow-redirect still needs a "
             "manual device test, but the URL scheme is viable.", ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())
