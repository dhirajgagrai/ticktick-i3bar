# ticktick-i3bar

Show the [TickTick](https://ticktick.com) desktop app's **pomodoro countdown** as text in your
i3 status bar.

```
W: down | E: 10.0.0.5 (1000 Mbit/s) | 2026-10-25 15:37:38 | POMO ▶ 23:41
```

## Why

On macOS, TickTick shows the running pomodoro's time next to its menu-bar icon. On Linux it
doesn't: Electron's `tray.setTitle()` is macOS-only, and TickTick doesn't draw the time into the
tray image on other platforms. The tray icon sits in your bar and tells you nothing.

`ticktick-i3bar` reads the timer state that the desktop app already keeps on disk and adds it
as a block at the end of your existing i3status output. You keep i3status and your existing
bar, add two config lines, and need nothing besides Python 3: no login and no API token.

## How it works

```
i3status ──JSON──▶ ticktick-i3bar ──JSON + pomo block──▶ i3bar
                         ▲
                         │ reads (read-only)
     ~/snap/ticktick/current/.config/ticktick/Local Storage/leveldb/
```

- TickTick's Electron renderer writes its timer to Chromium Local Storage (a leveldb) every few
  seconds: `pomoState` (status, running/paused, rendered time left) and `globalPomoState`
  (duration, plus a list of run fragments; the last one stays open while the timer runs).
- leveldb files are append-only, so the newest record for a key is the current state. The script
  scans the files directly, so it doesn't need a leveldb library or any third-party package.
- The time left is computed from the fragments, so it is accurate to the second.
- On play, TickTick saves `globalPomoState` straight away, but `pomoState` keeps saying `paused`
  until its next ~5 s render tick. The script therefore trusts whichever record was saved last,
  so the countdown starts as soon as the timer record reaches disk.
- The block is hidden when TickTick isn't running or no timer is active. Any error just hides the
  block; it can't break the rest of your bar.

### Why not the TickTick Open API?

The [Open API](https://developer.ticktick.com) only exposes **completed** focus records. Creating
one requires both a start and an end time, and there's no endpoint for a timer that's still
running. A live countdown is only available locally.

## What it shows

| State                    | Block              | Colour        |
|--------------------------|--------------------|---------------|
| Pomo running             | `POMO ▶ 23:41`     | bar default   |
| Pomo, last minute        | `POMO ▶ 00:42`     | red `#FF6E67` |
| Paused                   | `POMO ⏸ 58:35`     | grey `#888888`|
| Break running            | `BREAK ▶ 04:10`    | bar default   |
| Idle / TickTick closed   | *(hidden)*         |               |

The icons shown above are approximations. By default the block uses the
[Nerd Font](https://www.nerdfonts.com) play/pause glyphs (`U+F04B` / `U+F04C`), because both come
from the same font and so have the same width; the text doesn't shift between play and pause. If
no Nerd Font is installed they show as empty boxes; see [Configuration](#configuration).

## Requirements

- Linux with i3 and `i3status` (sway's swaybar speaks the same protocol, but this is untested)
- Python ≥ 3.8 (no third-party packages)
- TickTick desktop app. The default path assumes the **snap** install; for other installs see
  [Configuration](#configuration).

## Install

Choose one. Both put a `ticktick-i3bar` command in `~/.local/bin`.

### Option A: `make` (recommended)

```sh
git clone https://github.com/dhirajgagrai/ticktick-i3bar.git
cd ticktick-i3bar
make install            # copies to ~/.local/bin/ticktick-i3bar
```

- `make link` symlinks instead of copying, so a `git pull` takes effect immediately.
- `make install PREFIX=/usr/local` installs system-wide (run it with `sudo`).
- `make uninstall` removes it.

### Option B: `pipx`

```sh
sudo apt install pipx   # Ubuntu 24.04 blocks `pip install --user` (PEP 668); pipx is the supported route
pipx ensurepath
pipx install git+https://github.com/dhirajgagrai/ticktick-i3bar.git
```

Upgrade with `pipx upgrade ticktick-i3bar`. `uv tool install git+https://…` works the same way.

Make sure `~/.local/bin` is on the `PATH` that i3 sees. If it isn't, use the full path in the i3
config below.

## Configure

**1. `~/.config/i3status/config`**: force JSON output. i3status picks its output format from its
parent process, and once it's piped into another program it falls back to plain text:

```diff
 general {
+        output_format = "i3bar"
         colors = true
         interval = 1
 }
```

`interval = 1` is what makes the countdown tick every second. The script adds its block each time
i3status prints a line, so it updates as often as i3status does.

**2. `~/.config/i3/config`**: pipe i3status through the wrapper:

```diff
 bar {
-        status_command i3status
+        status_command i3status | ~/.local/bin/ticktick-i3bar
 }
```

**3.** Reload i3 with `$mod+Shift+r`.

## Configuration

| Variable           | Default                                       | Purpose                                     |
|--------------------|-----------------------------------------------|---------------------------------------------|
| `TICKTICK_PROFILE` | `~/snap/ticktick/current/.config/ticktick`    | TickTick's Electron user-data-dir           |
| `TICKTICK_ICON_RUNNING` | Nerd Font play `U+F04B`                  | Icon while the timer runs                   |
| `TICKTICK_ICON_PAUSED`  | Nerd Font pause `U+F04C`                 | Icon while paused                           |
| `TICKTICK_POSITION`     | `last`                                   | `last`: after i3status's blocks (right end); `first`: before them |

Without a Nerd Font, pick two symbols that your bar font draws itself, for example
`TICKTICK_ICON_RUNNING='>' TICKTICK_ICON_PAUSED='='`. Mixing symbols from different fonts (such
as `▶` from DejaVu with `⏸` from a fallback font) makes the block change width when you pause.
`fc-match "monospace:charset=23f8"` shows which font draws a given codepoint.

For a non-snap install, find the directory that contains `Local Storage/leveldb` (look in
`~/.config/` for a `ticktick` or `TickTick` folder, or check the `--user-data-dir=` argument in
`ps aux | grep ticktick`), then:

```
status_command i3status | env TICKTICK_PROFILE=$HOME/.config/ticktick ticktick-i3bar
```

Colours are the `COLOR_PAUSED` / `COLOR_ENDING` constants at the top of `ticktick_i3bar.py`.

## Troubleshooting

```sh
ticktick-i3bar --once                       # print the block (or null) and exit
i3status | ticktick-i3bar | head -4         # check the full pipe
```

- **`null` with a timer running:** check `TICKTICK_PROFILE`. TickTick must be running; the
  script checks the PID in its `SingletonLock`.
- **Bar shows plain text or an error:** `output_format = "i3bar"` is missing from the i3status
  config.

## Limitations

- **Lag after play/pause: about 5 s on play, 0–5 s on pause.** Chromium holds Local Storage
  writes and saves them to disk in batches, so a click reaches the file late. Measured against
  TickTick's own click times, play landed 5.0 s later every time; pause landed with the next
  batched save, which happens about every 5 s while the timer runs. The script picks up a change
  within one i3status interval (1 s) after it lands. Because the time is computed from the click
  time, the countdown is right as soon as it appears; only the switch between ▶ and ⏸ is late.
  Removing the lag would mean attaching to the running app (for example with
  `--remote-debugging-port`). That would let any local program control TickTick, which is too
  big a risk for a status bar.
- **Relies on undocumented app internals.** The storage keys and format belong to TickTick and may
  change in an update. If they do, the block disappears and the rest of the bar keeps working.
- **Break and idle states** are labelled from the `status` field. Only the pomo running/paused
  states have been checked against the real app so far.
- **Restarting TickTick with a timer paused** can hide the block until the timer moves again.
  On restart Chromium may move the data into compacted, compressed table files that the script
  doesn't decompress.
- Only the pomodoro timer is supported, not the stopwatch.

The script only reads local files; it never writes to TickTick's data or contacts the network.

## Development

```sh
make test        # stdlib unittest, uses a fake TickTick profile
make link        # run your checkout from ~/.local/bin
```

## License

[MIT](LICENSE)

This is an unofficial tool. It is not affiliated with, endorsed by, or supported by TickTick or
Appest Inc. "TickTick" is used only to describe what this tool works with.
