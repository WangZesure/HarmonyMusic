"""1.4 LRC quality gate (contract C4).

Reads the aligned LRCs (downloaded into `data/lrc/`) plus `_meta.json`, `selected.json`
and `lyrics.json`, normalizes every file with the canonical `common.normalize_lrc`,
scores it, and writes `data/lyrics_final.json`:

    { song_id: { lrc, lrc_source, lrc_quality, lyric_searchable, line_count, issues[] } }

`lrc_source` is `lrclib` / `aligned` / `asr` / `none` (C2.song_meta). `lyric_searchable`
is true only when source in (lrclib, aligned) and quality >= config.LRC_QUALITY_MIN.

Fetch the server output first:
    python data_pipeline/tools/gpu.py get /root/autodl-tmp/music/lrc data_pipeline/data/lrc
    python data_pipeline/05_qc_lrc.py
"""
from __future__ import annotations

import html
import sys
from collections import Counter

import config
import common

log = common.get_logger()

_METHOD_TO_SOURCE = {"lrclib_synced": "lrclib", "align": "aligned", "asr": "asr"}


def count_lines(text: str) -> int:
    return len([ln for ln in (text or "").splitlines() if ln.strip()])


def score(track: dict, raw_lrc: str, meta: dict, plain: str) -> dict:
    """Return the lyrics_final record for one track."""
    duration = track.get("duration")
    tid = str(track["id"])
    issues: list[str] = []
    raw_lrc = html.unescape(raw_lrc or "")  # source lyrics may carry &amp; etc.

    if track.get("kind") != "vocal":
        return {"lrc": "", "lrc_source": "none", "lrc_quality": None,
                "lyric_searchable": False, "line_count": 0, "issues": []}

    method = meta.get("method")
    lrc = common.normalize_lrc(raw_lrc, duration)
    rows = common.parse_lrc(lrc, duration)
    lrc_source = _METHOD_TO_SOURCE.get(method)
    if lrc_source is None:
        lrc_source = "aligned" if lrc and method not in ("download_failed", "align_failed") else "none"

    if not rows:
        issues.append("empty")
        return {"lrc": "", "lrc_source": "none", "lrc_quality": 0.0,
                "lyric_searchable": False, "line_count": 0, "issues": issues}

    # a line longer than song_lyric_lines.text can hold would force truncation,
    # which is forbidden -> drop the whole song's lyrics instead (QC exclusion).
    if any(len(r["text"]) > config.MAX_LYRIC_LINE_CHARS for r in rows):
        issues.append("line_too_long")
        return {"lrc": "", "lrc_source": "none", "lrc_quality": 0.0,
                "lyric_searchable": False, "line_count": 0, "issues": issues}

    q = 1.0
    if meta.get("ok") is False:
        q *= 0.5
        issues.append(f"04_status={method}")
    if duration and rows[-1]["start_ms"] >= int(duration * 1000):
        q *= 0.7
        issues.append("last>=duration")
    if meta.get("line_match") is False:
        q *= 0.6
        issues.append(f"line_count {meta.get('lines')}!={meta.get('src_lines')}")
    if lrc_source == "aligned" and not meta.get("line_match"):
        # fall back to a direct comparison if 04 did not record it
        src = count_lines(plain)
        if src and len(rows) != src:
            q *= 0.6
            issues.append(f"lines {len(rows)}!={src}")
    conf = meta.get("conf_mean")
    if isinstance(conf, (int, float)):
        q *= 0.5 + 0.5 * max(0.0, min(1.0, conf))

    q = round(max(0.0, min(1.0, q)), 3)
    searchable = lrc_source in ("lrclib", "aligned") and q >= config.LRC_QUALITY_MIN
    if lrc_source == "asr":
        issues.append("asr_not_searchable")
    return {"lrc": lrc, "lrc_source": lrc_source, "lrc_quality": q,
            "lyric_searchable": searchable, "line_count": len(rows), "issues": issues}


def main() -> int:
    selected = common.load_json(config.SELECTED_FILE)
    if not selected:
        log.error("data/selected.json missing - run 02_select.py first.")
        return 2
    lyrics = common.load_json(config.LYRICS_FILE, default={}) or {}
    meta = common.load_json(config.LRC_DIR / "_meta.json", default={}) or {}
    if not meta:
        log.error("data/lrc/_meta.json missing - download the server lrc/ dir first:\n"
                  "  python data_pipeline/tools/gpu.py get "
                  "/root/autodl-tmp/music/lrc data_pipeline/data/lrc")
        return 2

    out: dict[str, dict] = {}
    for t in selected:
        tid = str(t["id"])
        raw_path = config.LRC_DIR / f"{tid}.lrc"
        raw = raw_path.read_text(encoding="utf-8") if raw_path.exists() else ""
        plain = (lyrics.get(tid, {}) or {}).get("plain") or ""
        out[tid] = score(t, raw, meta.get(tid, {}), plain)

    common.save_json(config.LYRICS_FINAL_FILE, out)
    write_report(out)
    return 0


def write_report(out: dict) -> None:
    src_counts = Counter(r["lrc_source"] for r in out.values())
    searchable = sum(1 for r in out.values() if r["lyric_searchable"])
    qs = [r["lrc_quality"] for r in out.values()
          if r["lrc_source"] in ("lrclib", "aligned") and r["lrc_quality"] is not None]
    low = sum(1 for q in qs if q < config.LRC_QUALITY_MIN)
    issue_counts = Counter(i.split()[0] for r in out.values() for i in r["issues"])

    lines = ["# 05_qc_lrc report", "",
             f"- songs: {len(out)}",
             f"- lyric_searchable: {searchable}", ""]
    for s in ("lrclib", "aligned", "asr", "none"):
        lines.append(f"- source {s}: {src_counts.get(s, 0)}")
    if qs:
        lines += ["", f"- quality (lrclib/aligned): min={min(qs)} "
                      f"mean={round(sum(qs)/len(qs),3)} max={max(qs)}",
                  f"- below threshold ({config.LRC_QUALITY_MIN}): {low}"]
    if issue_counts:
        lines += ["", "## issues", ""] + [f"- {k}: {v}" for k, v in issue_counts.most_common()]

    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (config.REPORTS_DIR / "05_qc.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info("wrote %s", config.LYRICS_FINAL_FILE)
    log.info("  searchable=%d  %s", searchable, dict(src_counts))


if __name__ == "__main__":
    sys.exit(main())
