"""1.3b Generate per-line timestamps (LRC) — RUNS ON THE GPU SERVER.

Standalone on purpose: this file is uploaded to the server and run there with no
other repo modules on the path (only `requests`, `torch`, `stable-ts`, `demucs`
are required). Reads `selected.json` + `lyrics.json` from `--workdir`:

  * source == "lrclib_synced" -> emit that LRC verbatim (no audio, no GPU);
  * otherwise                 -> download the Jamendo stream, (optionally)
    separate the vocal stem with Demucs, then align the known lyrics text to the
    vocal stem with stable-ts; if there is no lyrics text, transcribe instead and
    mark the result as `asr`.

Outputs `lrc/{id}.lrc` and `lrc/_meta.json`.

    python 04_align.py --workdir /root/autodl-tmp/music --out lrc \
        [--limit N] [--no-demucs] [--compare] [--model large-v3] \
        [--language en] [--device cuda]

`--compare` also aligns the same text against the *original mix* and records the
mean per-line timestamp delta (a résumé-friendly "does vocal separation help?").
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import requests

# Direct huggingface.co is blocked on the GPU host; Demucs fetches its model via
# HF hub. Without this, every Demucs call stalls ~3 min on connect timeouts.
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

USER_AGENT = "HarmonyMusicDataPipeline/0.1 (non-commercial research)"

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("align")


def load_json(path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def sec_to_lrc(t: float) -> str:
    t = max(0.0, float(t))
    m, s = divmod(t, 60)
    return f"[{int(m):02d}:{s:05.2f}]"


def segments_to_lrc(segments) -> str:
    lines = []
    for seg in segments:
        text = (getattr(seg, "text", "") or "").strip()
        if text:
            lines.append(f"{sec_to_lrc(seg.start)}{text}")
    return "\n".join(lines) + ("\n" if lines else "")


def seg_confidence(seg) -> float | None:
    """Mean word probability of a segment, or None if the model gives none.

    Fed to 05 to compute `lrc_quality` (C4). Falls back to None -> 05 relies on
    structural checks (line coverage, monotonic timestamps) only.
    """
    words = getattr(seg, "words", None) or []
    probs = [getattr(w, "probability", None) for w in words]
    probs = [p for p in probs if p is not None]
    return sum(probs) / len(probs) if probs else None


def download_audio(url: str, dest: Path) -> bool:
    if dest.exists() and dest.stat().st_size > 1024:
        return True
    try:
        with requests.get(url, stream=True, timeout=60,
                          headers={"User-Agent": USER_AGENT}) as r:
            r.raise_for_status()
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("wb") as f:
                for chunk in r.iter_content(1 << 16):
                    f.write(chunk)
        return dest.stat().st_size > 1024
    except Exception as e:
        log.warning("download failed %s: %s", url[:60], e)
        return False


def separate_vocals(audio: Path, vocals_dir: Path) -> Path | None:
    """Demucs two-stem -> path to the vocals.wav, or None on failure."""
    vocals = vocals_dir / "htdemucs" / audio.stem / "vocals.wav"
    if vocals.exists():
        return vocals
    try:
        subprocess.run(
            [sys.executable, "-m", "demucs", "--two-stems=vocals",
             "-n", "htdemucs", "-o", str(vocals_dir), str(audio)],
            check=True, capture_output=True, env=dict(os.environ),
        )
    except subprocess.CalledProcessError as e:
        log.warning("demucs failed for %s: %s", audio.name,
                    (e.stderr or b"")[-300:].decode("utf-8", "replace"))
        return None
    return vocals if vocals.exists() else None


def mean_time_delta(lrc_a: str, lrc_b: str) -> float | None:
    """Mean |dt| (s) between two LRCs, matched line-by-line where text is equal."""
    def parse(lrc: str):
        out = []
        for ln in lrc.splitlines():
            if ln.startswith("[") and "]" in ln:
                ts, text = ln[1:].split("]", 1)
                try:
                    m, s = ts.split(":")
                    out.append((int(m) * 60 + float(s), text.strip()))
                except ValueError:
                    continue
        return out

    bmap: dict[str, list[float]] = {}
    for t, txt in parse(lrc_b):
        bmap.setdefault(txt, []).append(t)
    deltas = []
    for t, txt in parse(lrc_a):
        if bmap.get(txt):
            deltas.append(abs(t - bmap[txt].pop(0)))
    return sum(deltas) / len(deltas) if deltas else None


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="Align lyrics -> LRC (GPU server)")
    ap.add_argument("--workdir", default="/root/autodl-tmp/music")
    ap.add_argument("--selected", default=None)
    ap.add_argument("--lyrics", default=None)
    ap.add_argument("--out", default="lrc")
    ap.add_argument("--model", default="large-v3")
    ap.add_argument("--language", default=None)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-demucs", action="store_true")
    ap.add_argument("--compare", action="store_true")
    args = ap.parse_args()

    work = Path(args.workdir)
    selected = load_json(Path(args.selected) if args.selected else work / "selected.json")
    lyrics = load_json(Path(args.lyrics) if args.lyrics else work / "lyrics.json", default={})
    if not selected or not lyrics:
        log.error("need selected.json and lyrics.json in %s", work)
        return 2

    out_dir = Path(args.out) if Path(args.out).is_absolute() else work / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    meta_path = out_dir / "_meta.json"
    meta = load_json(meta_path, default={}) or {}

    direct, to_align = [], []
    for t in selected:
        tid = str(t["id"])
        rec = lyrics.get(tid, {})
        if t.get("kind") != "vocal" or rec.get("source") == "instrumental":
            continue
        (direct if rec.get("source") == "lrclib_synced" else to_align).append(t)
    if args.limit:
        to_align = to_align[: args.limit]

    for t in direct:
        tid = str(t["id"])
        (out_dir / f"{tid}.lrc").write_text(lyrics[tid]["synced"], encoding="utf-8")
        meta[tid] = {"method": "lrclib_synced", "ok": True}
    save_json(meta_path, meta)
    log.info("wrote %d direct (lrclib_synced); %d need alignment/ASR", len(direct), len(to_align))
    if not to_align:
        return 0

    log.info("loading stable-ts model %s on %s ...", args.model, args.device)
    import stable_whisper
    model = stable_whisper.load_model(args.model, device=args.device)

    ok = failed = 0
    for t in to_align:
        tid = str(t["id"])
        audio = work / "audio" / f"{tid}.mp3"
        if not download_audio(t["audio"], audio):
            meta[tid] = {"method": "download_failed", "ok": False}
            failed += 1
            continue

        vocal_src = audio
        if not args.no_demucs:
            v = separate_vocals(audio, work / "vocals")
            if v:
                vocal_src = v

        text = (lyrics[tid].get("plain") or "").strip()
        lang = args.language or (t.get("lang") if len(str(t.get("lang") or "")) == 2 else None)
        try:
            if text:
                # original_split=True keeps the input lyric line breaks, so the
                # LRC lines map 1:1 to the cleaned lyrics (C4 requirement).
                result = model.align(str(vocal_src), text, language=lang,
                                     original_split=True)
                method = "align"
            else:
                result = model.transcribe(str(vocal_src), language=lang)
                method = "asr"
            lrc = segments_to_lrc(result.segments)
        except Exception as e:
            log.warning("align failed for %s: %s", tid, e)
            meta[tid] = {"method": "align_failed", "ok": False, "error": str(e)[:200]}
            failed += 1
            continue

        (out_dir / f"{tid}.lrc").write_text(lrc, encoding="utf-8")
        confs = [c for c in (seg_confidence(s) for s in result.segments) if c is not None]
        rec = {"method": method, "ok": bool(lrc.strip()),
               "lines": len(lrc.strip().splitlines()), "demucs": not args.no_demucs,
               "src_lines": len(text.splitlines()) if text else None}
        if method == "align" and text:
            rec["line_match"] = rec["lines"] == len([l for l in text.splitlines() if l.strip()])
        if confs:
            rec["conf_mean"] = round(sum(confs) / len(confs), 4)
            rec["conf_min"] = round(min(confs), 4)
        if args.compare and text and vocal_src != audio:
            try:
                mix = model.align(str(audio), text, language=lang)
                rec["mean_delta_vs_mix_s"] = mean_time_delta(lrc, segments_to_lrc(mix.segments))
            except Exception:
                pass
        meta[tid] = rec
        ok += 1
        save_json(meta_path, meta)  # checkpoint after each track
        log.info("[%d/%d] %s -> %s (%d lines)", ok + failed, len(to_align), tid, method, rec["lines"])

    log.info("done: ok=%d failed=%d -> %s", ok, failed, out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
