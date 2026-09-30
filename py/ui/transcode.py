"""Transcoding with ffmpeg/ffprobe: tool detection, probing, and a single background job."""
from __future__ import annotations

import copy
import glob
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from .fileops import _path_under_base
from .scanner import recompute_status, refresh_transcoded
from .settings import DEFAULT_TRANSCODE, VIDEO_EXTS, load_config, log, transcoded_root, transcoded_root_conflict
from .state import _lock, _state, resolve_item

ENCODER_QUALITY_ARGS = {
    "hevc_amf":   lambda qp: ["-rc", "1", "-qp_i", str(qp), "-qp_p", str(qp)],
    "hevc_nvenc": lambda qp: ["-rc", "constqp", "-qp", str(qp)],
    "libx265":    lambda qp: ["-crf", str(qp)],
}

# Equivalent language tags; the last entry is the English name used to match track titles
LANGUAGE_ALIASES = [
    ("eng", "en", "english"), ("dan", "da", "danish"), ("ger", "deu", "de", "german"),
    ("fre", "fra", "fr", "french"), ("spa", "es", "spanish"), ("swe", "sv", "swedish"),
    ("nor", "nob", "nno", "no", "nb", "nn", "norwegian"), ("ita", "it", "italian"),
    ("dut", "nld", "nl", "dutch"), ("fin", "fi", "finnish"), ("por", "pt", "portuguese"),
    ("pol", "pl", "polish"), ("rus", "ru", "russian"), ("jpn", "ja", "japanese"),
    ("kor", "ko", "korean"), ("chi", "zho", "zh", "chinese"), ("hin", "hi", "hindi"),
    ("ara", "ar", "arabic"), ("tur", "tr", "turkish"),
]
REJECT_WORDS   = tuple(DEFAULT_TRANSCODE["audio_reject_words"])
REJECT_DISPOS  = ("comment", "hearing_impaired", "visual_impaired")
LOG_MAX_LINES  = 2000
PROGRESS_LOG_EVERY = 10.0   # seconds between progress lines in the console log

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_proc: Optional[subprocess.Popen] = None
_probed: Dict[str, Dict[str, Any]] = {}   # normalized src path → parsed probe
_log: deque = deque(maxlen=LOG_MAX_LINES)
_log_seq = 0   # total lines ever appended; lets clients fetch only new lines


# ── Pure helpers (unit-tested) ─────────────────────────────────────────────────
def _language_keys(code: str) -> Tuple[set, Optional[str]]:
    """All tags equivalent to `code`, plus the English language name (for title matching)."""
    code = code.strip().lower()
    for group in LANGUAGE_ALIASES:
        if code in group:
            return set(group), group[-1]
    return {code}, None


def language_matches(stream: Dict[str, Any], code: str) -> bool:
    keys, name = _language_keys(code)
    lang = (stream.get("language") or "").lower()
    if lang in keys or lang.split("-")[0] in keys:
        return True
    title = (stream.get("title") or "").lower()
    return lang in ("", "und") and bool(name) and name in title


def is_clean_track(stream: Dict[str, Any], reject_words=REJECT_WORDS) -> bool:
    title = (stream.get("title") or "").lower()
    if any(w.strip().lower() in title for w in reject_words if w.strip()):
        return False
    return not any(stream.get(d) for d in REJECT_DISPOS)


def suggest_audio(audio: List[Dict[str, Any]], languages=("eng",),
                  reject_words=REJECT_WORDS, prefer: str = "first") -> Optional[int]:
    """Absolute stream index of the best clean track in the first language that has one, or None."""
    for code in languages:
        if not code.strip():
            continue
        clean = [s for s in audio if language_matches(s, code) and is_clean_track(s, reject_words)]
        if not clean:
            continue
        if prefer == "default":
            pick = next((s for s in clean if s.get("default")), clean[0])
        elif prefer == "channels":
            pick = max(clean, key=lambda s: s.get("channels") or 0)   # first wins on ties
        else:
            pick = clean[0]
        return pick["index"]
    return None


def parse_probe(data: Dict[str, Any]) -> Dict[str, Any]:
    fmt = data.get("format") or {}
    try:
        duration = float(fmt.get("duration") or 0)
    except ValueError:
        duration = 0.0
    try:
        size = int(fmt.get("size") or 0)
    except ValueError:
        size = 0
    video = None
    audio: List[Dict[str, Any]] = []
    subtitles = 0
    for s in data.get("streams") or []:
        kind  = s.get("codec_type")
        tags  = s.get("tags") or {}
        dispo = s.get("disposition") or {}
        if kind == "video" and video is None and not dispo.get("attached_pic"):
            video = {"codec": s.get("codec_name"), "width": s.get("width"), "height": s.get("height")}
        elif kind == "audio":
            audio.append({
                "index":            s.get("index"),
                "codec":            s.get("codec_name"),
                "channels":         s.get("channels"),
                "language":         tags.get("language") or tags.get("LANGUAGE") or "",
                "title":            tags.get("title") or tags.get("TITLE") or "",
                "default":          bool(dispo.get("default")),
                "comment":          bool(dispo.get("comment")),
                "hearing_impaired": bool(dispo.get("hearing_impaired")),
                "visual_impaired":  bool(dispo.get("visual_impaired")),
            })
        elif kind == "subtitle":
            subtitles += 1
    return {"duration": duration, "size": size, "video": video, "audio": audio, "subtitles": subtitles}


def dest_for(src: str, item_id: str, item_path: str, output_root: str) -> Path:
    """`<root>/Movies/<movie folder>/<stem>.mkv`; loose files get a folder named after their stem."""
    movies = Path(output_root) / "Movies"
    s = Path(src)
    if item_id != item_path:   # loose-file items use the file path as id
        return movies / s.stem / f"{s.stem}.mkv"
    try:
        rel = s.relative_to(item_path)
    except ValueError:
        rel = Path(s.name)
    return movies / Path(item_path).name / rel.with_suffix(".mkv")


def build_ffmpeg_cmd(ffmpeg: str, src: str, dst: str, audio_index: int,
                     encoder: str, qp: int) -> List[str]:
    return [
        ffmpeg, "-hide_banner", "-nostdin", "-n",
        "-v", "error", "-nostats", "-progress", "pipe:1",
        "-i", src,
        "-map", "0:v:0", "-map", f"0:{audio_index}", "-map", "0:s?",
        "-c:v", encoder, *ENCODER_QUALITY_ARGS[encoder](qp),
        "-c:a", "copy", "-c:s", "copy",
        dst,
    ]


# ── Tool detection ─────────────────────────────────────────────────────────────
def _exe(name: str) -> str:
    return name + ".exe" if sys.platform == "win32" else name


def _clean_path(p: Optional[str]) -> str:
    """Strip whitespace and the quotes Windows' "Copy as path" adds."""
    return (p or "").strip().strip('"').strip()


def _candidate_dirs(hints: List[str]) -> List[str]:
    dirs = [p if os.path.isdir(p) else os.path.dirname(p) for p in hints if p]
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "")
        dirs += [
            r"C:\ffmpeg\bin", r"C:\tools\ffmpeg\bin", r"D:\tools\ffmpeg\bin",
            r"C:\Program Files\ffmpeg\bin",
            os.path.join(local, "Microsoft", "WinGet", "Links"),
            os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "chocolatey", "bin"),
            os.path.join(os.path.expanduser("~"), "scoop", "shims"),
        ]
        dirs += glob.glob(os.path.join(local, "Microsoft", "WinGet", "Packages", "*FFmpeg*", "*", "bin"))
    else:
        dirs += ["/usr/bin", "/usr/local/bin", "/opt/homebrew/bin"]
    return dirs


def _tool_version(path: str) -> Optional[str]:
    try:
        r = subprocess.run([path, "-version"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=10, creationflags=_NO_WINDOW)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    first = (r.stdout or "").splitlines()
    return first[0].strip() if first else None


def _verify_tool(name: str, path: str) -> Optional[Dict[str, str]]:
    """{path, version} when `path` (a file or its folder) is a working `name`, else None."""
    if not path:
        return None
    p = Path(path)
    if p.is_dir():
        p = p / _exe(name)
    if not p.is_file():
        return None
    version = _tool_version(str(p))
    if not version or not version.lower().startswith(f"{name} version"):
        return None
    return {"path": str(p), "version": version}


def _find_tool(name: str, hints: List[str]) -> Optional[Dict[str, str]]:
    found = shutil.which(name)
    candidates = ([found] if found else []) + [str(Path(d) / _exe(name)) for d in _candidate_dirs(hints)]
    for c in candidates:
        ok = _verify_tool(name, c)
        if ok:
            return ok
    return None


def detect_tools(entered: Dict[str, str]) -> Dict[str, Any]:
    """Verify the entered paths; search PATH and common folders for any that don't work."""
    paths = {name: _clean_path(entered.get(f"{name}_path")) for name in ("ffmpeg", "ffprobe")}
    hints = [p for p in paths.values() if p]
    out: Dict[str, Any] = {}
    for name, path in paths.items():
        ok = _verify_tool(name, path)
        if ok:
            out[name] = {**ok, "source": "verified"}
            continue
        found = _find_tool(name, hints)
        out[name] = {
            "path":    found["path"] if found else None,
            "version": found["version"] if found else None,
            "source":  "detected" if found else "missing",
            "entered_invalid": bool(path),
        }
    for name, r in out.items():
        log.info("Tool check %s: entered=%r → %s %r", name, paths[name], r["source"], r["path"])
    return out


def _test_encoder(ffmpeg: str, encoder: str, qp: int) -> Dict[str, Any]:
    """Encode 10 generated frames to the null muxer — proves the encoder (and GPU/driver) works."""
    cmd = [
        ffmpeg, "-hide_banner", "-nostdin", "-v", "error",
        "-f", "lavfi", "-i", "color=c=black:s=1280x720:r=25",
        "-frames:v", "10", "-c:v", encoder, *ENCODER_QUALITY_ARGS[encoder](qp),
        "-f", "null", "-",
    ]
    started = time.monotonic()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=60, creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return {"encoder": encoder, "ok": False, "seconds": 60.0, "reason": "Timed out after 60 s",
                "error": "Timed out after 60 s"}
    except OSError as exc:
        return {"encoder": encoder, "ok": False, "seconds": 0.0, "reason": "Cannot start ffmpeg",
                "error": f"Cannot start ffmpeg: {exc}"}
    seconds = round(time.monotonic() - started, 2)
    lines = [l.strip() for l in (r.stderr or "").splitlines() if l.strip()]
    if r.returncode == 0:
        return {"encoder": encoder, "ok": True, "seconds": seconds, "error": None, "reason": None}
    return {"encoder": encoder, "ok": False, "seconds": seconds, "reason": _encoder_failure_reason(lines),
            "error": "\n".join(lines[:4]) or f"ffmpeg exit code {r.returncode}"}


def _encoder_failure_reason(lines: List[str]) -> str:
    text = "\n".join(lines).lower()
    if "unknown encoder" in text:
        return "This ffmpeg build does not include this encoder."
    if "nvcuda.dll" in text or "no nvenc capable devices" in text or "cuda" in text:
        return "No NVIDIA GPU or NVIDIA driver found on this machine."
    if "amfrt" in text or ("amf" in text and ("load" in text or "init" in text)):
        return "No AMD GPU or AMD driver found on this machine."
    return "The encoder could not be started with these settings — see ffmpeg's message below."


def test_encoders(ffmpeg_path: str, encoders: List[str], qp: int) -> Dict[str, Any]:
    ffmpeg = _verify_tool("ffmpeg", _clean_path(ffmpeg_path))
    if not ffmpeg:
        raise HTTPException(400, f"ffmpeg path does not work: {ffmpeg_path or '(empty)'} — use Detect / Verify first")
    results = []
    for enc in encoders:
        res = _test_encoder(ffmpeg["path"], enc, qp)
        log.info("Encoder test %s (QP %d): %s in %.2fs%s", enc, qp, "OK" if res["ok"] else "FAILED",
                 res["seconds"], "" if res["ok"] else f" — {res['reason']} ({res['error'].splitlines()[0]})")
        results.append(res)
    return {"ffmpeg": ffmpeg["path"], "results": results}


# ── Probe ──────────────────────────────────────────────────────────────────────
def _key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _ffprobe_json(ffprobe: str, path: str) -> Dict[str, Any]:
    cmd = [
        ffprobe, "-v", "error",
        "-show_entries",
        "format=duration,size"
        ":stream=index,codec_type,codec_name,channels,width,height"
        ":stream_tags=language,title"
        ":stream_disposition=default,comment,hearing_impaired,visual_impaired,attached_pic",
        "-of", "json", path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=120, creationflags=_NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "").strip() or f"ffprobe exit code {r.returncode}")
    return json.loads(r.stdout or "{}")


def probe_items(item_ids: List[str]) -> Dict[str, Any]:
    tc = load_config()["transcode"]
    ffprobe = _clean_path(tc.get("ffprobe_path"))
    if not ffprobe or not Path(ffprobe).is_file():
        raise HTTPException(400, "ffprobe path is not configured or does not exist — see Settings → Transcoding")
    out_root = _clean_path(tc.get("output_root"))

    results: List[Dict[str, Any]] = []
    for item_id in item_ids:
        item = resolve_item(item_id)
        if not item:
            results.append({"item_id": item_id, "name": item_id, "error": "Item not found"})
            continue
        if item["type"] != "movie":
            results.append({"item_id": item_id, "name": item["folder"],
                            "error": "Series are not supported yet"})
            continue
        if item["location"] == "transcoded":
            results.append({"item_id": item_id, "name": item["folder"],
                            "error": "Already transcoded"})
            continue
        for src in item["files"]:
            if Path(src).suffix.lower() not in VIDEO_EXTS:
                continue
            entry: Dict[str, Any] = {"item_id": item_id, "title": item["title"],
                                     "src": src, "name": Path(src).name}
            try:
                info = parse_probe(_ffprobe_json(ffprobe, src))
            except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
                entry["error"] = str(exc)
                results.append(entry)
                continue
            with _lock:
                _probed[_key(src)] = info
            entry.update(info)
            entry["suggested_audio"] = suggest_audio(
                info["audio"], tc["audio_languages"], tc["audio_reject_words"], tc["audio_prefer"])
            if out_root:
                dst = dest_for(src, item["id"], item["path"], out_root)
                entry["dst"] = str(dst)
                entry["dst_exists"] = dst.exists()
            results.append(entry)
    return {
        "config": {k: tc.get(k) for k in ("encoder", "qp", "output_root", "audio_languages",
                                          "audio_reject_words", "audio_prefer")},
        "files":  results,
    }


# ── Job log ────────────────────────────────────────────────────────────────────
def _append_log(line: str) -> None:
    global _log_seq
    with _lock:
        _log.append(line)
        _log_seq += 1


def job_log(since: int = 0) -> Dict[str, Any]:
    with _lock:
        lines = list(_log)
        seq = _log_seq
    first = seq - len(lines)
    return {"seq": seq, "lines": lines[max(since - first, 0):]}


# ── Job control ────────────────────────────────────────────────────────────────
def start_job(files: List[Dict[str, Any]]) -> Dict[str, Any]:
    tc = load_config()["transcode"]
    ffmpeg   = _clean_path(tc.get("ffmpeg_path"))
    out_root = _clean_path(tc.get("output_root"))
    encoder  = tc.get("encoder")
    qp       = tc.get("qp")
    if not ffmpeg or not Path(ffmpeg).is_file():
        raise HTTPException(400, "ffmpeg path is not configured or does not exist — see Settings → Transcoding")
    if not out_root:
        raise HTTPException(400, "Transcode output folder is not configured — see Settings → Transcoding")
    if encoder not in ENCODER_QUALITY_ARGS:
        raise HTTPException(400, f"Unsupported encoder: {encoder}")
    if not isinstance(qp, int) or not 0 <= qp <= 51:
        raise HTTPException(400, "QP must be an integer between 0 and 51")
    if not files:
        raise HTTPException(400, "No files to transcode")
    conflict = transcoded_root_conflict(load_config())
    if conflict:
        raise HTTPException(400, conflict)

    entries: List[Dict[str, Any]] = []
    seen_dst: set = set()
    for f in files:
        item = resolve_item(f["item_id"])
        if not item:
            raise HTTPException(400, f"Item not found: {f['item_id']}")
        if item["type"] != "movie":
            raise HTTPException(400, f"Series are not supported yet: {item['title']}")
        if item["location"] == "transcoded":
            raise HTTPException(400, f"Already transcoded: {item['title']}")
        if f["src"] not in item["files"]:
            raise HTTPException(400, f"File does not belong to item: {f['src']}")
        with _lock:
            info = _probed.get(_key(f["src"]))
        if not info:
            raise HTTPException(400, f"File must be probed before transcoding: {f['src']}")
        if f["audio_index"] not in {a["index"] for a in info["audio"]}:
            raise HTTPException(400, f"Audio stream {f['audio_index']} not found in {f['src']}")
        dst = dest_for(f["src"], item["id"], item["path"], out_root)
        if dst.exists():
            skip = "Destination already exists"
        elif _key(str(dst)) in seen_dst:
            skip = "Another file in this job has the same destination"
        else:
            skip = None
        seen_dst.add(_key(str(dst)))
        entries.append({
            "item_id":     item["id"],
            "title":       item["title"],
            "src":         f["src"],
            "dst":         str(dst),
            "audio_index": f["audio_index"],
            "duration":    info["duration"],
            "status":      "skipped" if skip else "pending",
            "progress":    0.0,
            "speed":       None,
            "error":       skip,
        })

    job = {
        "running":  True,
        "killed":   False,
        "error":    None,
        "started":  datetime.now().isoformat(),
        "finished": None,
        "current":  None,
        "encoder":  encoder,
        "qp":       qp,
        "files":    entries,
    }
    with _lock:
        current = _state["transcode"]
        if current and current["running"]:
            raise HTTPException(409, "A transcoding job is already running")
        _state["transcode"] = job
        _log.clear()
    for sub in ("Movies", "Series"):
        try:
            (Path(out_root) / sub).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log.warning("Cannot create %s: %s", Path(out_root) / sub, exc)
    threading.Thread(target=_run_job, args=(job, ffmpeg), daemon=True, name="transcoder").start()
    log.info("Transcode job started: %d file(s)", len(entries))
    return {"ok": True, "files": len(entries)}


def kill_job() -> Dict[str, Any]:
    with _lock:
        job = _state["transcode"]
        if not job or not job["running"]:
            raise HTTPException(409, "No transcoding job is running")
        job["killed"] = True
        proc = _proc
    _append_log("✖ Kill requested by user")
    if proc and proc.poll() is None:
        proc.kill()
    return {"ok": True}


def job_status() -> Dict[str, Any]:
    with _lock:
        job = copy.deepcopy(_state["transcode"])
        seq = _log_seq
    if not job:
        return {"running": False, "started": None, "files": [], "overall": 0.0, "log_seq": seq}
    active = [f for f in job["files"] if f["status"] != "skipped"]
    job["overall"] = sum(f["progress"] for f in active) / len(active) if active else 1.0
    job["log_seq"] = seq
    return job


# ── Worker ─────────────────────────────────────────────────────────────────────
def _run_job(job: Dict[str, Any], ffmpeg: str) -> None:
    global _proc
    _append_log(f"=== Transcode started {job['started']} — encoder {job['encoder']}, QP {job['qp']} ===")
    try:
        for idx, f in enumerate(job["files"]):
            with _lock:
                if f["status"] == "pending" and job["killed"]:
                    f["status"] = "cancelled"
                elif f["status"] == "pending":
                    job["current"] = idx
                    f["status"] = "running"
            if f["status"] == "skipped":
                _append_log(f"— Skipped {f['src']}: {f['error']}")
            elif f["status"] == "running":
                _transcode_file(job, f, ffmpeg)
    except Exception as exc:
        log.error("Transcode job failed: %s", exc)
        with _lock:
            job["error"] = str(exc)
        _append_log(f"[ERROR] {exc}")
    finally:
        if any(f["status"] == "done" for f in job["files"]):
            _append_log("Refreshing the transcoded folder…")
            try:
                refresh_transcoded()
            except Exception as exc:
                log.error("Transcoded-folder refresh failed: %s", exc)
                _append_log(f"[ERROR] Transcoded-folder refresh failed: {exc}")
        with _lock:
            job["running"]  = False
            job["current"]  = None
            job["finished"] = datetime.now().isoformat()
            _proc = None
            counts: Dict[str, int] = {}
            for f in job["files"]:
                counts[f["status"]] = counts.get(f["status"], 0) + 1
        summary = ", ".join(f"{n} {s}" for s, n in sorted(counts.items()))
        _append_log(f"=== Transcode finished — {summary} ===")
        log.info("Transcode job finished: %s", summary)


def _pump_stderr(stream) -> None:
    for line in stream:
        line = line.rstrip()
        if line:
            _append_log(line)


def _delete_partial(dst: Path) -> None:
    try:
        if dst.exists():
            dst.unlink()
            _append_log(f"  Deleted partial file {dst}")
        dst.parent.rmdir()   # only succeeds when empty
    except OSError:
        pass


def _fmt_secs(secs: float) -> str:
    secs = int(secs)
    return f"{secs // 3600:02d}:{secs % 3600 // 60:02d}:{secs % 60:02d}"


def _transcode_file(job: Dict[str, Any], f: Dict[str, Any], ffmpeg: str) -> None:
    global _proc
    dst = Path(f["dst"])
    _append_log("")
    _append_log(f"▶ {f['src']}")
    _append_log(f"  → {dst}  (audio stream #{f['audio_index']})")
    # Re-check right before encoding: a failed run deletes dst, so it must be ours
    if dst.exists():
        with _lock:
            f["status"], f["error"] = "skipped", "Destination already exists"
        _append_log(f"  — Skipped: {f['error']}")
        return
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        with _lock:
            f["status"], f["error"] = "failed", f"Cannot create folder: {exc}"
        _append_log(f"  [ERROR] {f['error']}")
        return

    cmd = build_ffmpeg_cmd(ffmpeg, f["src"], str(dst), f["audio_index"], job["encoder"], job["qp"])
    _append_log("  $ " + subprocess.list2cmdline(cmd))
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                errors="replace", bufsize=1, creationflags=_NO_WINDOW)
    except OSError as exc:
        with _lock:
            f["status"], f["error"] = "failed", f"Cannot start ffmpeg: {exc}"
        _append_log(f"  [ERROR] {f['error']}")
        return

    with _lock:
        _proc = proc
        killed_early = job["killed"]
    if killed_early:
        proc.kill()

    err_thread = threading.Thread(target=_pump_stderr, args=(proc.stderr,), daemon=True)
    err_thread.start()

    duration = f["duration"] or 0
    last_log = 0.0
    block: Dict[str, str] = {}
    for line in proc.stdout:
        key, _, val = line.strip().partition("=")
        block[key] = val
        if key in ("out_time_us", "out_time_ms") and duration:
            try:
                secs = int(val) / 1_000_000   # both keys are microseconds
            except ValueError:
                continue
            with _lock:
                f["progress"] = min(max(secs / duration, 0.0), 1.0)
        elif key == "progress":
            with _lock:
                f["speed"] = block.get("speed")
                pct = f["progress"] * 100
            now = time.monotonic()
            if now - last_log >= PROGRESS_LOG_EVERY or val == "end":
                last_log = now
                t = block.get("out_time_us") or block.get("out_time_ms") or "0"
                try:
                    pos = _fmt_secs(int(t) / 1_000_000)
                except ValueError:
                    pos = "?"
                _append_log(f"  {pct:5.1f}%  time={pos}/{_fmt_secs(duration)}  "
                            f"fps={block.get('fps', '?')}  speed={block.get('speed', '?')}")
            block = {}

    rc = proc.wait()
    err_thread.join(timeout=5)
    with _lock:
        _proc = None
        killed = job["killed"]
    if killed or rc != 0:
        _delete_partial(dst)
        with _lock:
            f["status"] = "killed" if killed else "failed"
            f["error"]  = "Killed by user" if killed else f"ffmpeg exit code {rc}"
        _append_log(f"  [{f['status'].upper()}] {f['error']}")
    else:
        with _lock:
            f["status"], f["progress"] = "done", 1.0
        _append_log("  ✔ Done")


# ── Use transcoded video ───────────────────────────────────────────────────────
def _match_key(path: str, item: Dict[str, Any]) -> str:
    """Comparison key for pairing videos: path relative to the item folder, without extension."""
    if item["id"] != item["path"]:   # loose file item
        return os.path.normcase(Path(path).stem)
    try:
        rel = os.path.relpath(path, item["path"])
    except ValueError:
        rel = os.path.basename(path)
    return os.path.normcase(os.path.splitext(rel)[0])


def pair_videos(original: Dict[str, Any], transcoded: Dict[str, Any]) -> List[Tuple[str, str]]:
    """(original video, transcoded video) pairs; raises 400 when a file cannot be paired unambiguously."""
    originals: Dict[str, str] = {}
    for f in original["files"]:
        if Path(f).suffix.lower() not in VIDEO_EXTS:
            continue
        key = _match_key(f, original)
        if key in originals:
            raise HTTPException(400, f"Ambiguous original videos: {originals[key]} and {f}")
        originals[key] = f
    pairs = []
    for t in transcoded["files"]:
        if Path(t).suffix.lower() not in VIDEO_EXTS:
            continue
        o = originals.get(_match_key(t, transcoded))
        if not o:
            raise HTTPException(400, f"No original video matches {Path(t).name}")
        pairs.append((o, t))
    if not pairs:
        raise HTTPException(400, "The transcoded item has no video files")
    return pairs


def _verify_transcoded(ffprobe: str, pairs: List[Tuple[str, str]]) -> None:
    """400 unless every transcoded file has video + audio and the same duration as its original."""
    if not ffprobe or not Path(ffprobe).is_file():
        raise HTTPException(400, "ffprobe is needed to verify the transcoded file — see Settings → Transcoding")
    for o, t in pairs:
        try:
            po, pt = parse_probe(_ffprobe_json(ffprobe, o)), parse_probe(_ffprobe_json(ffprobe, t))
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
            raise HTTPException(400, f"Could not verify {Path(t).name}: {exc}")
        if not pt["video"] or not pt["audio"]:
            raise HTTPException(400, f"Transcoded file has no video or no audio stream: {Path(t).name}")
        do, dt = po["duration"], pt["duration"]
        if not dt or (do and abs(do - dt) > max(2.0, do * 0.01)):
            raise HTTPException(400, f"Duration mismatch — original {_fmt_secs(do)}, transcoded {_fmt_secs(dt)}; "
                                     f"the transcoded file may be incomplete: {Path(t).name}")


def use_transcoded(original_id: str, transcoded_id: str, dry_run: bool) -> Dict[str, Any]:
    """Replace the original's video(s) with the transcoded file(s); NFO/images stay; transcoded folder is deleted."""
    config = load_config()
    root = transcoded_root(config)
    conflict = transcoded_root_conflict(config)
    if conflict:
        raise HTTPException(400, conflict)
    original   = resolve_item(original_id)
    transcoded = resolve_item(transcoded_id)
    if not original or not transcoded:
        raise HTTPException(404, "Item not found")
    if transcoded["location"] != "transcoded" or original["location"] == "transcoded":
        raise HTTPException(400, "Pick one original and one transcoded copy")
    if original["type"] != "movie" or transcoded["type"] != "movie":
        raise HTTPException(400, "Series are not supported yet")
    with _lock:
        job = _state["transcode"]
        if job and job["running"]:
            raise HTTPException(409, "Wait until the running transcoding job has finished")
    if not _path_under_base(Path(original["path"]), config.get("locations", []) + config.get("downloads", [])):
        raise HTTPException(400, "Original is outside the configured locations")
    tr_path = Path(transcoded["path"])
    tr_is_folder = transcoded["id"] == transcoded["path"]
    try:
        depth = len(tr_path.relative_to(root).parts) if root else 0
    except ValueError:
        depth = 0
    # A folder item must sit at least at <root>\Movies\<movie> so the delete never hits the root itself
    if depth < (2 if tr_is_folder else 1):
        raise HTTPException(400, "Transcoded item is not inside the transcode output folder")

    pairs = pair_videos(original, transcoded)
    _verify_transcoded(_clean_path(config["transcode"].get("ffprobe_path")), pairs)
    replaced = {os.path.normcase(o) for o, _ in pairs}
    plan = []
    changes: List[Dict[str, str]] = []
    for o, t in pairs:
        new = str(Path(o).with_suffix(".mkv"))
        if os.path.normcase(new) not in replaced and Path(new).exists():
            raise HTTPException(400, f"Target already exists and is not the video being replaced: {new}")
        plan.append((o, t, new))
        changes.append({"type": "replace", "from": t, "to": new})
        if os.path.normcase(new) != os.path.normcase(o):
            changes.append({"type": "delete", "path": o})
    if tr_is_folder:
        changes.append({"type": "delete-folder", "path": str(tr_path)})
    if dry_run:
        return {"ok": True, "dry_run": True, "changes": changes}

    new_files: Dict[str, str] = {}
    for o, t, new in plan:
        tmp = Path(new).with_name(Path(new).name + ".partial")
        try:
            shutil.move(t, str(tmp))   # copies across drives, so this can take a while
        except OSError as exc:
            try:
                if tmp.exists() and Path(t).exists():
                    tmp.unlink()   # incomplete copy; the transcoded file is still in place
            except OSError:
                pass
            log.error("Use transcoded failed for %s: %s", o, exc)
            raise HTTPException(500, f"Copying the transcoded file failed (nothing replaced): {exc}")
        try:
            os.replace(tmp, new)
        except OSError as exc:
            try:
                shutil.move(str(tmp), t)   # put the transcoded file back
            except OSError:
                log.error("Could not move %s back to %s", tmp, t)
            log.error("Use transcoded failed for %s: %s", o, exc)
            raise HTTPException(500, f"Replacing {o} failed (original untouched): {exc}")
        if os.path.normcase(new) != os.path.normcase(o):
            try:
                Path(o).unlink()
            except OSError as exc:
                log.warning("Replaced, but could not delete old original %s: %s", o, exc)
        new_files[o] = new
        log.info("Use transcoded: %s → %s", t, new)
    if tr_is_folder:
        try:
            shutil.rmtree(tr_path)
            log.info("Use transcoded: deleted %s", tr_path)
        except OSError as exc:
            log.warning("Could not delete transcoded folder %s: %s", tr_path, exc)

    with _lock:
        original["files"] = [new_files.get(f, f) for f in original["files"]]
        _state["transcoded"] = [i for i in _state["transcoded"] if i["id"] != transcoded["id"]]
    recompute_status()
    return {"ok": True, "dry_run": False, "changes": changes}
