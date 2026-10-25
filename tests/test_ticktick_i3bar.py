import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "ticktick_i3bar.py")
sys.path.insert(0, ROOT)

import ticktick_i3bar as it  # noqa: E402


def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def put(key, value, utf16=False):
    """A leveldb WriteBatch Put record as Chromium's localStorage writes it."""
    k = b"_file://\x01user@example.com/" + key
    text = json.dumps(value, ensure_ascii=False)
    v = b"\x00" + text.encode("utf-16-le") if utf16 else b"\x01" + text.encode("latin-1")
    return b"\x01" + _varint(len(k)) + k + _varint(len(v)) + v


def delete(key):
    k = b"_file://\x01user@example.com/" + key
    return b"\x00" + _varint(len(k)) + k + b"\xaa\xbb\xcc\xdd"


def pomo(preview, interval="progress", status="pomo"):
    return {"status": status, "intervalStatus": interval,
            "renderData": {"timePreview": preview}, "continuePomo": False}


class FakeProfile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.profile = self.tmp.name
        self.leveldb = os.path.join(self.profile, "Local Storage", "leveldb")
        os.makedirs(self.leveldb)
        self.set_running(True)
        it.PROFILE, it.LEVELDB = self.profile, self.leveldb
        it.ICON_RUNNING, it.ICON_PAUSED = "▶", "⏸"
        it._cache.update(sig=None, data=None)

    def tearDown(self):
        self.tmp.cleanup()

    def set_running(self, running):
        lock = os.path.join(self.profile, "SingletonLock")
        if os.path.lexists(lock):
            os.remove(lock)
        os.symlink(f"host-{os.getpid() if running else 999999999}", lock)

    def write_log(self, data, name="000003.log"):
        with open(os.path.join(self.leveldb, name), "wb") as f:
            f.write(data)
        it._cache.update(sig=None, data=None)

    def global_state(self, remaining_s, duration_s=1500, running=True):
        """Timer with one closed 60s fragment and a last fragment open iff running."""
        now_ms = int(time.time() * 1000)
        last_len = (duration_s - remaining_s - 60) * 1000
        frags = [{"startTime": now_ms - 600000, "endTime": now_ms - 540000}]
        if running:
            frags.append({"startTime": now_ms - last_len})
        else:
            frags.append({"startTime": now_ms - last_len - 30000, "endTime": now_ms - 30000})
        return {"duration": duration_s * 1000, "fragments": frags}


class BlockTests(FakeProfile):
    def test_paused(self):
        self.write_log(put(b"pomoState", pomo("58:35", "paused")))
        b = it.pomo_block()
        self.assertEqual(b["full_text"], "POMO ⏸ 58:35")
        self.assertEqual(b["color"], it.COLOR_PAUSED)

    def test_running_uses_exact_time_from_global_state(self):
        self.write_log(put(b"globalPomoState", self.global_state(1380))
                       + put(b"pomoState", pomo("23:02")))
        self.assertEqual(it.pomo_block()["full_text"], "POMO ▶ 23:00")

    def test_running_falls_back_when_global_state_disagrees(self):
        # Stale globalPomoState from an older session must not win.
        self.write_log(put(b"globalPomoState", self.global_state(100))
                       + put(b"pomoState", pomo("23:00")))
        self.assertEqual(it.pomo_block()["full_text"], "POMO ▶ 23:00")

    def test_play_shows_running_before_pomo_state_catches_up(self):
        # On play, globalPomoState is written first; pomoState still says paused.
        self.write_log(put(b"pomoState", pomo("23:05", "paused"))
                       + put(b"globalPomoState", self.global_state(1385)))
        b = it.pomo_block()
        self.assertEqual(b["full_text"], "POMO ▶ 23:05")
        self.assertNotIn("color", b)

    def test_fresh_start_from_idle(self):
        self.write_log(put(b"pomoState", pomo("25:00", interval="init"))
                       + put(b"globalPomoState", self.global_state(1499)))
        self.assertEqual(it.pomo_block()["full_text"], "POMO ▶ 24:59")

    def test_newer_closed_timer_without_active_pomo_hides_block(self):
        self.write_log(put(b"pomoState", pomo("25:00", interval="init"))
                       + put(b"globalPomoState", self.global_state(600, running=False)))
        self.assertIsNone(it.pomo_block())

    def test_newer_overdue_timer_hides_block(self):
        self.write_log(put(b"pomoState", pomo("00:05"))
                       + put(b"globalPomoState", self.global_state(-60)))
        self.assertIsNone(it.pomo_block())

    def test_pause_after_play(self):
        self.write_log(put(b"globalPomoState", self.global_state(1385))
                       + put(b"pomoState", pomo("23:05", "paused")))
        self.assertEqual(it.pomo_block()["full_text"], "POMO ⏸ 23:05")

    def test_last_minute_is_coloured(self):
        self.write_log(put(b"pomoState", pomo("00:42")))
        b = it.pomo_block()
        self.assertEqual(b["full_text"], "POMO ▶ 00:42")
        self.assertEqual(b["color"], it.COLOR_ENDING)

    def test_break(self):
        self.write_log(put(b"pomoState", pomo("04:10", status="shortBreak")))
        self.assertEqual(it.pomo_block()["full_text"], "BREAK ▶ 04:10")

    def test_newest_record_wins(self):
        self.write_log(put(b"pomoState", pomo("10:00", "paused"))
                       + put(b"pomoState", pomo("09:00", "paused")))
        self.assertEqual(it.pomo_block()["full_text"], "POMO ⏸ 09:00")

    def test_log_is_newer_than_table(self):
        self.write_log(put(b"pomoState", pomo("10:00", "paused")), "000005.ldb")
        self.write_log(put(b"pomoState", pomo("09:00", "paused")), "000007.log")
        self.assertEqual(it.pomo_block()["full_text"], "POMO ⏸ 09:00")

    def test_log_is_newer_than_higher_numbered_table(self):
        # Table #5 flushed from log #3 while #4 was already the live log.
        self.write_log(put(b"pomoState", pomo("09:00", "paused")), "000004.log")
        self.write_log(put(b"pomoState", pomo("10:00", "paused")), "000005.ldb")
        self.assertEqual(it.pomo_block()["full_text"], "POMO ⏸ 09:00")

    def test_utf16_value(self):
        self.write_log(put(b"pomoState", pomo("12:34", "paused"), utf16=True))
        self.assertEqual(it.pomo_block()["full_text"], "POMO ⏸ 12:34")

    def test_deleted_key_hides_block(self):
        self.write_log(put(b"pomoState", pomo("22:58")) + delete(b"pomoState"))
        self.assertIsNone(it.pomo_block())

    def test_idle_state_hides_block(self):
        self.write_log(put(b"pomoState", pomo("25:00", interval="init")))
        self.assertIsNone(it.pomo_block())

    def test_app_not_running_hides_block(self):
        self.write_log(put(b"pomoState", pomo("22:58")))
        self.set_running(False)
        self.assertIsNone(it.pomo_block())

    def test_missing_profile_hides_block(self):
        it.PROFILE = it.LEVELDB = os.path.join(self.profile, "nope")
        self.assertIsNone(it.safe_block())

    def test_hours_format(self):
        self.write_log(put(b"pomoState", pomo("1:05:00", "paused")))
        self.assertEqual(it.pomo_block()["full_text"], "POMO ⏸ 1:05:00")


class PipeTests(FakeProfile):
    I3STATUS = (
        '{"version":1}\n'
        "[\n"
        '[{"name":"tztime","full_text":"12:00:00"}]\n'
        ',[{"name":"tztime","full_text":"12:00:01"}]\n'
    )

    def run_pipe(self, stdin, **extra_env):
        env = dict(os.environ, TICKTICK_PROFILE=self.profile,
                   TICKTICK_ICON_RUNNING="▶", TICKTICK_ICON_PAUSED="⏸", **extra_env)
        return subprocess.run([sys.executable, SCRIPT], input=stdin, env=env,
                              capture_output=True, text=True, timeout=10)

    def test_injects_block_and_keeps_protocol(self):
        self.write_log(put(b"pomoState", pomo("05:00", "paused")))
        r = self.run_pipe(self.I3STATUS)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        self.assertEqual(lines[:2], ['{"version":1}', "["])
        first = json.loads(lines[2])
        self.assertTrue(lines[3].startswith(","))
        second = json.loads(lines[3][1:])
        for blocks in (first, second):
            self.assertEqual(blocks[0]["name"], "tztime")
            self.assertEqual(blocks[-1]["full_text"], "POMO ⏸ 05:00")

    def test_position_first(self):
        self.write_log(put(b"pomoState", pomo("05:00", "paused")))
        r = self.run_pipe(self.I3STATUS, TICKTICK_POSITION="first")
        blocks = json.loads(r.stdout.splitlines()[2])
        self.assertEqual(blocks[0]["full_text"], "POMO ⏸ 05:00")
        self.assertEqual(blocks[1]["name"], "tztime")

    def test_passthrough_without_timer(self):
        r = self.run_pipe(self.I3STATUS)
        self.assertEqual(r.stdout, self.I3STATUS)

    def test_non_json_lines_pass_through(self):
        r = self.run_pipe("W: down | 12:00:00\n" * 3)
        self.assertEqual(r.stdout, "W: down | 12:00:00\n" * 3)


if __name__ == "__main__":
    unittest.main()
