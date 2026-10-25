#!/usr/bin/env python3
"""Wrap i3status and add a TickTick pomodoro countdown block.

Usage (i3 config):  status_command i3status | ticktick-i3bar
Debug:              ticktick-i3bar --once   (print the block and exit)
Profile override:   TICKTICK_PROFILE=/path/to/ticktick/user-data-dir

TickTick's Electron renderer persists timer state to Chromium Local Storage
(a leveldb under the snap's user-data-dir) every few seconds:
  <user>/pomoState        {"status":"pomo","intervalStatus":"progress"|"paused",
                           "renderData":{"timePreview":"58:35",...}}
  <user>/globalPomoState  {"duration":ms,"fragments":[{"startTime":ms,"endTime":ms},
                           ...,{"startTime":ms}]}   (open last fragment = running)
leveldb log/table files are append-ordered, so the last record for a key wins.

On play, globalPomoState is written at once but pomoState still says "paused"
until the app's next ~5s render tick, so whichever record is newer decides.
Chromium itself batches localStorage commits by a few seconds; that lag is
unavoidable when reading from disk.
"""
import glob
import json
import os
import re
import sys
import time

PROFILE = os.path.expanduser(
    os.environ.get("TICKTICK_PROFILE", "~/snap/ticktick/current/.config/ticktick"))
LEVELDB = os.path.join(PROFILE, "Local Storage", "leveldb")

COLOR_PAUSED = "#888888"
COLOR_ENDING = "#FF6E67"  # last minute

# Pick both icons from one font, or the block shifts width on play/pause.
# Defaults are Nerd Font play/pause (nf-fa-play, nf-fa-pause).
ICON_RUNNING = os.environ.get("TICKTICK_ICON_RUNNING", "")
ICON_PAUSED = os.environ.get("TICKTICK_ICON_PAUSED", "")

# "last" puts the block after i3status's blocks (right end), "first" before them.
POSITION = os.environ.get("TICKTICK_POSITION", "last")

_cache = {"sig": None, "data": None}


def ticktick_running():
    # SingletonLock -> "<hostname>-<pid>"; it can outlive a crash, so check the pid.
    try:
        pid = os.readlink(os.path.join(PROFILE, "SingletonLock")).rsplit("-", 1)[1]
    except (OSError, IndexError):
        return False
    return os.path.exists(f"/proc/{pid}")


def _decode(raw):
    # Chromium localStorage values: \x01 prefix = Latin-1, \x00 = UTF-16LE.
    if raw[:1] == b"\x01":
        return raw[1:].decode("latin-1")
    if raw[:1] == b"\x00":
        return raw[1:].decode("utf-16-le", "ignore")
    return None


def _last_value(blob, key):
    """(position, newest JSON value) for a localStorage key suffix; value None if absent/deleted.

    After the key, a .log Put has a length varint and a .ldb entry an 8-byte
    sequence tag; either way the value starts within a few bytes. A deletion has
    no value, so if the newest occurrence doesn't parse, the key is gone.
    """
    pos = blob.rfind(b"/" + key)
    if pos < 0:
        return -1, None
    end = pos + len(key) + 1
    window = blob[end:end + 12]
    starts = [i for i in (window.find(b"\x01{"), window.find(b"\x00{\x00")) if i >= 0]
    if not starts:
        return pos, None
    text = _decode(blob[end + min(starts):end + min(starts) + 16384])
    try:
        return pos, json.JSONDecoder().raw_decode(text)[0]
    except (TypeError, ValueError):
        return pos, None


def read_state():
    files = glob.glob(os.path.join(LEVELDB, "*.ldb")) + glob.glob(os.path.join(LEVELDB, "*.log"))
    files.sort(key=lambda p: int(re.sub(r"\D", "", os.path.basename(p)) or 0))
    try:
        sig = tuple((p, os.stat(p).st_mtime_ns, os.stat(p).st_size) for p in files)
    except OSError:
        return None
    if sig != _cache["sig"]:
        blob = b""
        for p in files:
            try:
                with open(p, "rb") as f:
                    blob += f.read()
            except OSError:
                pass
        pomo_pos, pomo = _last_value(blob, b"pomoState")
        global_pos, global_ = _last_value(blob, b"globalPomoState")
        _cache["sig"] = sig
        _cache["data"] = {
            "pomo": pomo,
            "global": global_,
            "global_newer": global_pos > pomo_pos,
            "mtime": max((s[1] for s in sig), default=0) / 1e9,
        }
    return _cache["data"]


def _timer_from_fragments(g, now):
    """(running, seconds left) from globalPomoState's fragments, or None."""
    try:
        frags = g["fragments"]
        now_ms = now * 1000
        elapsed = sum((f.get("endTime") or now_ms) - f["startTime"] for f in frags)
        return "endTime" not in frags[-1], (g["duration"] - elapsed) / 1000
    except (KeyError, TypeError, IndexError):
        return None


def _mmss_to_secs(s):
    try:
        parts = [int(x) for x in s.split(":")]
    except (AttributeError, ValueError):
        return None
    secs = 0
    for p in parts:
        secs = secs * 60 + p
    return secs


def _fmt(secs):
    secs = max(0, int(round(secs)))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def pomo_block():
    if not ticktick_running():
        return None
    st = read_state()
    if not st:
        return None
    now = time.time()
    pomo = st["pomo"] if isinstance(st["pomo"], dict) else {}
    interval = pomo.get("intervalStatus")
    active = interval in ("progress", "paused")
    timer = _timer_from_fragments(st["global"], now)

    if st["global_newer"] and timer:
        # Timer changed after the last render (typically play): trust the timer.
        running, remaining = timer
        if (not running and not active) or remaining < -5:
            return None  # stopped or finished; pomoState just hasn't caught up
        label = "POMO"
    else:
        if not active:
            return None
        preview = _mmss_to_secs((pomo.get("renderData") or {}).get("timePreview"))
        if preview is None:
            return None
        status = str(pomo.get("status", ""))
        label = "POMO" if status == "pomo" else ("BREAK" if "break" in status.lower() else status.upper())
        running = interval == "progress"
        remaining = preview
        if running:
            # Fallback: last rendered value, aged by how long ago the store was written.
            remaining = preview - max(0.0, now - st["mtime"])
            # Preferred: exact from the timer, if it agrees with what was rendered
            # (guards against a stale timer from an earlier session).
            if status == "pomo" and timer and timer[0] and remaining - 3 <= timer[1] <= preview + 3:
                remaining = timer[1]

    if not running:
        return {"name": "ticktick", "full_text": f"{label} {ICON_PAUSED} {_fmt(remaining)}",
                "color": COLOR_PAUSED}
    block = {"name": "ticktick", "full_text": f"{label} {ICON_RUNNING} {_fmt(remaining)}"}
    if remaining <= 60:
        block["color"] = COLOR_ENDING
    return block


def safe_block():
    try:
        return pomo_block()
    except Exception:  # never take the whole bar down
        return None


def main():
    if "--once" in sys.argv:
        print(json.dumps(safe_block(), ensure_ascii=False))
        return

    out = sys.stdout
    # Header + opening "[" pass through untouched.
    for _ in range(2):
        line = sys.stdin.readline()
        if not line:
            return
        out.write(line)
    out.flush()

    for line in sys.stdin:
        prefix = ""
        body = line.strip()
        if body.startswith(","):
            prefix, body = ",", body[1:]
        try:
            blocks = json.loads(body)
        except ValueError:
            out.write(line)
            out.flush()
            continue
        b = safe_block()
        if b:
            blocks.insert(0 if POSITION == "first" else len(blocks), b)
            line = prefix + json.dumps(blocks, ensure_ascii=False) + "\n"
        out.write(line)
        out.flush()


def run():
    try:
        main()
    except (BrokenPipeError, KeyboardInterrupt):
        # i3bar closed the pipe (bar reload/restart): exit quietly.
        try:
            sys.stdout = open(os.devnull, "w")
        except OSError:
            pass
        sys.exit(0)


if __name__ == "__main__":
    run()
