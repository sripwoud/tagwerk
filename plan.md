# tagwerk — implementation plan

_Written 2026-09-09 after a requirements grill in a `~` session; grilled again the same day with `/grill-with-docs` against `docs/research/2026-09-09-solution-space.md`, which produced `CONTEXT.md` and `docs/adr/`. Status: sections A to E approved, nothing built. Next: `/to-spec`, then `/to-tickets`. Decisions below are final unless you reopen them; the "Facts" section was verified on lechuck on 2026-09-09._

## 1. Problem

Record hours actually worked, attributed per repo, with day/week/month charts. Two consumers: a monthly invoice to Exodus and a burnout check. Hard requirement: zero manual start/stop. Taskwarrior + timewarrior + bugwarrior was the incumbent and died of manual discipline: 214 timew intervals Mar–Jul 2026 (73, 49, 57, 31, 4 per month), last one 2026-07-15.

Tool survey verdict: nothing off the shelf fits. Every CLI tracker is manual. wakapi + terminal-wakatime is heartbeat-native but web-UI only; it keys durations on a project hash with a 10 min timeout, so no split and no double counting, and rules live in its code. ActivityWatch + awatcher gives passive presence but title-only attribution, and edits only through its REST API. Build ~350 lines of Python on top of what the platform already provides: Hyprland (focus, pid), kitty remote control (cwd), hypridle (idle), Claude Code and pi (session hooks). Survey with sources: `docs/research/2026-09-09-solution-space.md`; decision: ADR-0001.

## 2. Decisions

| Topic             | Decision                                                                                                                                                                                                                                          |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Present minute    | not idle (hypridle 150 s, inhibitors honoured) and a poll within 2 min. Every present minute is credited exactly once                                                                                                                             |
| Repo signals      | agent beats (Claude, pi) lease their repo for 10 min; a focused kitty cwd or a GitHub repo in a browser title leases for 1 min                                                                                                                    |
| Concurrency       | wall-clock total; each present minute split evenly across leased repos                                                                                                                                                                            |
| Project from cwd  | longest matching root wins; project = first path component under the root with `.<suffix>` stripped (`assets.8467` → `assets`); cwd equal to the root → `general`                                                                                 |
| Kinds             | `work` (roots `~/code/exodus`, `~/memories/work`), `personal` (root `~/code`), `off` (manual only). Present minute with no leased repo → the ambient bucket (`work/general` when the focused title matches a work pattern), else `personal/other` |
| Unattended agents | beats while idle book nothing; attribution resumes on return                                                                                                                                                                                      |
| Store             | own JSONL, `~/.local/share/tagwerk/YYYY-MM.jsonl`, UTC timestamps, append-only. Corrections are appended `span` records, never mutations                                                                                                          |
| History           | one-shot import of timew intervals as spans, then uninstall timewarrior                                                                                                                                                                           |
| Sensors           | `hypridle` listener at 150 s plus sleep marks (idle/active); 15 s focus poller as a systemd user service; kitty cwd via `kitten @ ls` over the Omarchy per-pid socket                                                                             |
| Idle inhibitors   | honoured (`ignore_dbus_inhibit = false`): a Meet call with hands off the keyboard stays present; an abandoned video over-counts the chart, not the invoice                                                                                        |
| Reports           | `today`, `week`, `month`, `invoice`, `fix`, `import-timew`; terminal bars; 8 h/day and 40 h/week caps, weekends highlighted                                                                                                                       |
| Invoice           | monthly markdown table, work kinds only, total rounded to 15 min, per-project largest remainder                                                                                                                                                   |
| Burnout           | passive: chart highlights only. Notifications later if the chart gets ignored                                                                                                                                                                     |
| Tasks             | out of scope. Parked in `~/knowledge/areas/dev/task-management-rethink.md`                                                                                                                                                                        |
| Language          | Python 3 stdlib only, shebang `#!/usr/bin/python3 -I`. Public repo `sripwoud/tagwerk`, MIT                                                                                                                                                        |
| Not doing         | web UI, sync, per-issue granularity, `--json` output, notifications, second machine, backfill of 2026-07-15 to rollout (no open invoice; Claude prompt history and pi session headers cover the gap if ever wanted)                               |

Why `#!/usr/bin/python3 -I` and not `env python3`: the mise shim `~/.local/share/mise/shims/python3` precedes `/usr/bin` on PATH, so a hook fired inside a worktree with a pinned Python would run the wrong interpreter (same failure that crashed pi via a repo `.nvmrc`). `-I` also ignores `PYTHONPATH`, `PYTHON*` and user site-packages. Verified: isolated flag on, Python 3.14.7.

## 3. Facts (verified 2026-09-09)

- Exodus repos: `~/code/exodus/movement/<repo>[.<suffix>]`, suffix is an issue number or branch (`assets.8467`, `assets.ag-fix-own-change-unconfirmed-spendable`). Also `~/code/exodus/<repo>` for a few. Personal: `~/code/<repo>[.<suffix>]` (`auberge.665`, `pi-memsearch.92`).
- `hyprctl activewindow -j` → `class`, `title`, `pid`, also `address`, `initialClass`, `inhibitingIdle`. Returns `{}` when nothing is focused (plain mode prints `Invalid`; Hyprland `src/ipc/s1/Commands.cpp:652-656`). Kitty class is `kitty`. Claude sets the kitty title (e.g. `✳ Claude Code`), so titles are useless for cwd.
- Kitty remote control: `/etc/xdg/kitty/kitty.conf` lines 22-23 set `allow_remote_control socket-only` and `listen_on unix:${XDG_RUNTIME_DIR}/omarchy-kitty-{kitty_pid}` system-wide (user `kitty.conf` line 35 duplicates `listen_on`). `kitten @ --to <socket> ls` works from outside kitty. Output nests `os_windows[].tabs[].windows[]`; every level has `is_active` (the active one within its parent) and `is_focused` (OS focus, false at every level while another app is focused, so select by `is_active`). A window has `cwd`, `at_prompt`, `foreground_processes[].{cwd,cmdline,pid}`. Verified: kitty 67316 → active window cwd `~/code/tagwerk`, foreground `claude`.
- systemd user environment carries `HYPRLAND_INSTANCE_SIGNATURE`, `WAYLAND_DISPLAY`, `XDG_RUNTIME_DIR` (uwsm imports them). `graphical-session.target` is active. A user service can call `hyprctl`. Its `PATH` includes `~/.local/bin`, so `hypridle.conf` can call `tagwerk` by name.
- `hypridle` 0.1.8-2 in Arch extra, not installed. Upstream ships a user unit `PartOf=graphical-session.target`, `After=graphical-session.target`, `ConditionEnvironment=WAYLAND_DISPLAY`, `Restart=on-failure`. Omarchy 4 does lock/screen-off from quickshell (`IdleMonitor`: screensaver at 150 s with window class `org.omarchy.screensaver`, lock at 300 s); hypridle here only runs the listener and sleep commands. `omarchy-shell idle status` exposes the same flag but is alpha internals. Hyprland config is Lua (`~/.config/hypr/*.lua`); `hypridle.conf` is a separate standard file. Use the `omarchy` skill when touching `~/.config/hypr`.
- Claude Code hook stdin JSON includes `cwd`. Existing hooks in `~/.claude/settings.json`: `PreToolUse` → `exodus-pretool-hook`, `rtk hook claude`. Add ours without touching those.
- pi extension API as used in `~/.pi/agent/extensions/*.ts`: `export default function (pi: ExtensionAPI)`, events `session_start`, `turn_start`, `tool_execution_end`, `agent_settled`, `session_shutdown`; `process.cwd()` is the session cwd. pi itself is a Bun binary (aqua), so the extension only needs `child_process.spawn` of the absolute `tagwerk` path.
- Timewarrior data: `~/.local/share/timewarrior/data/2026-0{3..7}.data`, `timew export` gives JSON with `start`, `end`, `tags`. Tag scheme: `exodus`, `project:<repo>`, free text.
- Zombie timers still running: `bugwarrior-pull.timer` (daily, 47 s CPU), `tw-today-reset.timer` (05:00). Disable during rollout. Also `/usr/lib/systemd/system-sleep/timew-stop`, unowned by any package, runs `timew stop` as the user on every suspend.
- This clone has no `hk` git hooks installed: run `hk install --mise` once. CI (`master.yml`) runs `hk check --all` only, so tests are not in CI until commit 1 adds `mise run test`.
- Editor nvim, terminal kitty, shell bash, browser Vivaldi (class `vivaldi-stable`). GitHub titles look like `<title> · Issue #N · ExodusMovement/assets`.
- Repo has `AGENTS.md` and `docs/agents/` from the mattpocock skill scaffold (GitHub issues as tracker). Glossary: `CONTEXT.md`. Decisions with trade-offs: `docs/adr/0001` to `0004`. Use the glossary's terms in code, tests and docs.

## 4. Data model

One JSON object per line. `ts` is UTC ISO 8601 with `Z`. Written by `append_event`, which injects `ts`.

```
{"ts":"...","ev":"focus","class":"kitty","title":"...","cwd":"/home/s/code/exodus/movement/assets.8467"}   cwd null unless class is kitty
{"ts":"...","ev":"idle"}
{"ts":"...","ev":"active"}
{"ts":"...","ev":"beat","src":"claude","cwd":"/home/s/code/exodus/movement/assets.8467"}
{"ts":"...","ev":"span","start":"...Z","end":"...Z","kind":"work","project":"assets","src":"fix"}          kind: work | personal | off; src: fix | timew
```

Focus poller writes a `focus` line (a poll) when (class, title, cwd) changed, and at least once every 60 s otherwise. A poll within 2 min is what proves the machine was on; suspend and shutdown leave a gap attribution reads as absent, and hypridle's sleep mark adds an explicit `idle` before suspend.

Volume: ~1.5k focus lines/day, beats throttled to one per (src, cwd) per 60 s, so a few thousand lines/day, a few MB/month. Reports rescan on the fly, no derived store.

## 5. Config `~/.config/tagwerk/config.toml`

```toml
data_dir = "~/.local/share/tagwerk" # default, override with TAGWERK_DATA_DIR
poll_sec = 15
poll_stale_min = 2
beat_lease_min = 10
focus_lease_min = 1
beat_throttle_sec = 60
day_cap_h = 8
week_cap_h = 40
kitty_socket = "unix:${XDG_RUNTIME_DIR}/omarchy-kitty-{pid}" # Omarchy default; {pid} is the focused kitty's pid

[roots] # longest prefix wins
"~/code/exodus/movement" = "work"
"~/code/exodus" = "work"
"~/memories/work" = "work"
"~/code" = "personal"

[[title]] # first match wins; only consulted when cwd gave nothing
pattern = 'ExodusMovement/(?P<project>[\w.-]+)'
kind = "work"

[[title]]
pattern = '(?i)slack|exodus|zoom|meet\.google|bitbucket'
kind = "work"
project = "general"
```

`TAGWERK_CONFIG` and `TAGWERK_DATA_DIR` env vars override paths; tests use them. Parsed with `tomllib`.

## 6. A) File layout

```
~/code/tagwerk/
├── tagwerk.py                 single module, ~350 lines, executable, shebang #!/usr/bin/python3 -I
├── test_tagwerk.py            pytest, fixtures are inline event lists and one captured kitten @ ls blob
├── config.example.toml        section 5 verbatim
├── README.md                  install, rollout checklist (section 12), command reference
├── contrib/
│   ├── hypridle.conf          general block (sleep marks, ignore_dbus_inhibit = false) + listener at 150 s
│   ├── tagwerk-focus.service  systemd user unit for the poller, ExecStart=%h/.local/bin/tagwerk focus
│   ├── claude-hooks.json      hooks fragment to merge into ~/.claude/settings.json, timeout 5 per hook
│   └── pi/tagwerk.ts          pi extension, ~30 lines
└── already present: pyproject.toml (ruff line 120, dev group pytest), .gitignore, .config/mise.toml, hk.pkl,
    .dprint.jsonc, renovate.json, .github/workflows/, AGENTS.md, docs/agents/, docs/research/, CONTEXT.md,
    docs/adr/0001-0004, plan.md
```

Install is a symlink: `~/.local/bin/tagwerk -> ~/code/tagwerk/tagwerk.py`. Adapters are symlinked or copied into dotfiles by hand (chezmoi tracks them).

## 7. B) Function structure (`tagwerk.py`)

Sections in file order. Types: `Bucket = tuple[str, str]` = `(kind, project)`.

Config

- `Config` frozen dataclass: fields from section 5 plus `roots: list[tuple[Path, str]]` sorted longest first, `titles: list[tuple[re.Pattern, str, str | None]]`.
- `load_config(path: Path) -> Config`

Store

- `data_dir(cfg) -> Path`
- `month_file(cfg, day: date) -> Path`
- `append_event(cfg, event: dict) -> None`
- `read_events(cfg, start: datetime, end: datetime) -> list[dict]` sorted by `ts`, from the month files covering `[start - 1 day, end]`

Resolution

- `resolve_cwd(cfg, cwd: str | None) -> Bucket | None`
- `resolve_title(cfg, cls: str, title: str) -> Bucket | None`
- `is_repo(bucket) -> bool` (project not in `general`, `other`)

Sensors

- `active_window() -> tuple[str, str, int] | None` via `hyprctl activewindow -j`
- `parse_kitty_ls(os_windows: list) -> str | None` pure: active OS window → active tab → active window → foreground cwd, else window cwd
- `kitty_cwd(cfg, pid: int) -> str | None` runs `kitten @ --to <socket> ls`, feeds `parse_kitty_ls`
- `cmd_focus(cfg)` loop
- `cmd_beat(cfg, src: str, cwd: str | None)` throttled append
- `cmd_idle(cfg)`, `cmd_active(cfg)`

Attribution

- `attribute(cfg, events, start: datetime, end: datetime) -> dict[date, dict[Bucket, float]]` minutes per local date per bucket

Reports

- `cmd_today(cfg)`, `cmd_week(cfg, offset: int)`, `cmd_month(cfg, month: str | None)`, `cmd_invoice(cfg, month: str)`
- `render_bar(segments: list[tuple[Bucket, float]], scale_h: float, width: int, cap_h: float) -> str`
- `color(bucket) -> str` stable per project, greys for personal, empty when `NO_COLOR` or not a tty
- `quarter_hours(totals: dict[str, float]) -> dict[str, float]` largest remainder on 0.25 h units

Corrections

- `cmd_fix(cfg, start: str, end: str, project: str, kind: str)`
- `cmd_import_timew(cfg, export_json: str | None)`

CLI

- `main(argv) -> int` argparse subparsers: `today`, `week [-n N]`, `month [YYYY-MM]`, `invoice YYYY-MM`, `fix START END PROJECT [--personal|--off]`, `import-timew [FILE]`, `beat SRC [--cwd PATH]`, `focus`, `idle`, `active`.

## 8. C) Pseudocode

`load_config(path)`

- read TOML; expand `~` in roots and `data_dir`; sort roots by path length descending; compile title patterns
- missing file → `SystemExit` with the path; bad pattern → let `re.error` propagate

`append_event(cfg, event)`

- `event["ts"] = now_utc().isoformat(timespec="seconds").replace("+00:00", "Z")`
- mkdir data dir; open month file for `ts` in append mode; write one `json.dumps(event)` line

`read_events(cfg, start, end)`

- for each month from `(start - 1 day)` to `end`: if file exists, parse each line
- return sorted by `ts`; malformed line → raise with file and line number

`resolve_cwd(cfg, cwd)`

- `None` if cwd is None
- find first root where `cwd == root` or `cwd` starts with `root/`
- none → `None`
- relative path empty → `(kind, "general")`
- project = first component, split on first `.`, keep left part
- return `(kind, project)`

`resolve_title(cfg, cls, title)`

- for each title rule in order: `m = pattern.search(title)`; if match: project = rule.project or `m.group("project")`; return `(kind, project)`
- no match → `None`

`kitty_cwd(cfg, pid)`

- `socket = os.path.expandvars(cfg.kitty_socket).format(pid=pid)`
- `subprocess.run(["kitten", "@", "--to", socket, "ls"], capture_output=True, text=True, timeout=2)`
- non-zero exit → `None` (kitty started without the Omarchy conf; cwd unknown, poller keeps going). Malformed JSON → raise, systemd restarts the unit
- else `parse_kitty_ls(json.loads(stdout))`

`parse_kitty_ls(os_windows)`

- first OS window with `is_active`, then its first tab with `is_active`, then its first window with `is_active`; nothing at any level → `None`
- `fg = win["foreground_processes"]`; return `fg[0]["cwd"]` if `fg` else `win["cwd"]`
- `is_focused` is ignored on purpose: it is false at every level while another app has focus

`active_window()`

- `subprocess.run(["hyprctl", "activewindow", "-j"], capture_output, text, timeout=2)`
- empty object → `None`; else `(class, title, pid)`

`cmd_focus(cfg)`

- `last = None; last_write = 0`
- loop: `w = active_window()`; cwd = `kitty_cwd(cfg, pid)` if class == `kitty` else None
- `cur = (class, title, cwd)`; if `cur != last` or `now - last_write >= 60`: append `focus`; update
- sleep `poll_sec`; `hyprctl` failure → let it raise, systemd restarts the unit

`cmd_beat(cfg, src, cwd)`

- cwd from `--cwd`, else from stdin JSON `cwd` (Claude hook), else `os.getcwd()`
- if `resolve_cwd(cfg, cwd)` is `None` → exit 0 (not under any root, nothing to attribute)
- stamp = `$XDG_RUNTIME_DIR/tagwerk/<src>-<sha1(cwd)[:12]>`; if exists and `now - mtime < beat_throttle_sec` → exit 0
- touch stamp; append `beat`

`cmd_idle` / `cmd_active`

- append `{"ev": "idle"}` / `{"ev": "active"}`

`contrib/hypridle.conf`

```
general {
    before_sleep_cmd = tagwerk idle
    after_sleep_cmd = tagwerk active
    ignore_dbus_inhibit = false
}

listener {
    timeout = 150
    on-timeout = tagwerk idle
    on-resume = tagwerk active
}
```

`attribute(cfg, events, start, end)`

- split events into `spans` and `stream`; `i = 0`
- state: `idle = False`, `last_poll = None`, `ambient: Bucket | None = None`, `leases: dict[Bucket, datetime]` expiry per repo bucket
- for `m` in every minute from `start` to `end`:
  - while `stream[i].ts <= m`: apply event:
    - `idle` → `idle = True`; `active` → `idle = False`
    - `focus` → `last_poll = ts`; `b = resolve_cwd(cwd) or resolve_title(class, title)`; if `b` and `is_repo(b)`: `leases[b] = ts + focus_lease`; `ambient = b if b and not is_repo(b) else None`
    - `beat` → `b = resolve_cwd(cwd)`; if `b` and `is_repo(b)`: `leases[b] = ts + beat_lease`
  - drop `leases` entries with expiry `<= m`
  - span covering `m` (`start <= m < end`, latest appended wins) → if kind `off`: continue; else credit 1.0 to `(kind, project)`; continue
  - `present = not idle and last_poll and m - last_poll <= poll_stale`; not present → continue
  - `leases` non-empty → credit `1 / len(leases)` to each
  - else `ambient` → credit 1.0 to it
  - else credit 1.0 to `("personal", "other")`
  - credit goes to `m.astimezone().date()`
- return totals
- ponytail: O(minutes × events) single pass, a month is 43k minutes, fine for years of data

`cmd_today(cfg)` / `cmd_week` / `cmd_month`

- compute local range (today; Monday..Sunday of the week offset by `-n`; first..last day of month); convert bounds to UTC
- `totals = attribute(cfg, read_events(...), start, end)`
- `today`: table `project  h:mm` sorted desc, personal rows after work rows, work subtotal, total
- `week`: one line per day: `Mon 08 ` + `render_bar` + ` 7.8h`; day label red when work+personal > `day_cap_h`, or weekend with any minutes; footer `work 38.5h / 40h`
- `month`: one bar per ISO week with the same rules, then the `today`-style table for the month
- `render_bar`: scale = 12 h across `width` cells; each bucket gets `round(minutes / scale_minutes * width)` cells of `█` in its color; a `│` marker at the cap position; empty cells `·`
- `color`: work projects → 256-color palette index by `zlib.crc32(project) % len(palette)`; `general` → blue; personal → grey; disabled when `NO_COLOR` set or stdout not a tty

`cmd_invoice(cfg, month)`

- totals for the month, keep kind `work` only, sum by project in hours
- `quarter_hours`: `total_q = round(total * 4)`; each project gets `floor(h * 4)`; distribute `total_q - sum(floors)` quarters to largest fractional remainders; divide by 4
- print markdown table `| Project | Hours |`, rows desc, `| **Total** | **N** |`

`cmd_fix(cfg, start, end, project, kind)`

- parse `HH:MM` (today, local) or `YYYY-MM-DDTHH:MM` (local); end ≤ start → error
- append `span` with UTC bounds, `src: "fix"`

`cmd_import_timew(cfg, export_json)`

- read JSON from file arg or `timew export`
- refuse if any existing `span` with `src == "timew"` exists (idempotency guard)
- per interval with `end`: project = tag with prefix `project:` stripped, else `general`; kind = `work` if `exodus` in tags else `personal`
- append spans with `src: "timew"`; print count

`main(argv)`

- build parser; `cfg = load_config(Path(os.environ.get("TAGWERK_CONFIG", "~/.config/tagwerk/config.toml")))`
- dispatch; return 0

Before writing `render_bar` and `color`, load the `dataviz` skill for palette and bar conventions.

## 9. D) Tests

Runner: `uv run pytest -q` from the `pyproject.toml` dev group, with `mypy` added there. Lint via the repo's hk steps (`mise run check` runs dprint and ruff). Types: `uv run mypy --strict tagwerk.py`. Commit 1 adds a mise task `test` (pytest + mypy) and a CI step `mise run test` with `aqua:astral-sh/uv` and `python` in `install_args`. All tests deterministic, no network, filesystem only via `tmp_path` with `TAGWERK_DATA_DIR`/`TAGWERK_CONFIG` set through `monkeypatch`. Fixed timestamps, no `now()` in unit tests (`attribute` takes explicit bounds; `append_event` gets a `now` parameter with a default).

Unit (`test_tagwerk.py`)

| Test                                             | Input → expected                                                                                                                    |
| ------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------- |
| `test_resolve_cwd_strips_worktree_suffix`        | `~/code/exodus/movement/assets.8467/src` → `("work","assets")`                                                                      |
| `test_resolve_cwd_longest_root_wins`             | `~/code/exodus/movement/x` → work, not personal via `~/code`                                                                        |
| `test_resolve_cwd_root_itself_is_general`        | `~/code/exodus/movement` → `("work","general")`                                                                                     |
| `test_resolve_cwd_personal_repo`                 | `~/code/auberge.665` → `("personal","auberge")`                                                                                     |
| `test_resolve_cwd_outside_roots`                 | `~/knowledge/inbox` → `None`; `None` → `None`                                                                                       |
| `test_resolve_title_github_repo`                 | `Fix · Issue #1 · ExodusMovement/assets - Vivaldi` → `("work","assets")`                                                            |
| `test_resolve_title_slack_is_general`            | `#next-lending - Exodus - Slack` → `("work","general")`                                                                             |
| `test_resolve_title_no_match`                    | `mesa marmol verde - Google Suche` → `None`                                                                                         |
| `test_attribute_single_beat_full_credit`         | focus poll + one beat in `assets`, 10 min present → 10.0 to assets                                                                  |
| `test_attribute_two_repos_split_evenly`          | beats in assets and exodus-checkout → 5.0 each over 10 min                                                                          |
| `test_attribute_beat_expires_after_lease`        | beat at t0, present until t0+15 → 10 to repo, 5 to ambient/other                                                                    |
| `test_attribute_idle_books_nothing`              | idle at t0+5 → 5 minutes only                                                                                                       |
| `test_attribute_unattended_agent_books_nothing`  | idle, then beats → 0; active again → credit resumes with recent set                                                                 |
| `test_attribute_missing_poll_is_absent`          | last focus at t0, no lines until t0+10 → only first 2 min counted                                                                   |
| `test_attribute_focus_cwd_counts_as_repo_signal` | focus in kitty with repo cwd, no beats → repo credited, 1 min lease                                                                 |
| `test_attribute_ambient_fallback`                | focus on Slack title, no repos → `("work","general")`                                                                               |
| `test_attribute_other_fallback`                  | focus on unmatched Vivaldi title → `("personal","other")`                                                                           |
| `test_attribute_span_overrides_sensors`          | span assets 14:00–15:00 over beats in checkout → 60 to assets                                                                       |
| `test_attribute_span_off_removes_time`           | span off → 0 minutes despite presence                                                                                               |
| `test_attribute_buckets_by_local_date`           | UTC 23:30–00:30 with TZ=Europe/Berlin → all on the same local date                                                                  |
| `test_append_and_read_roundtrip_month_boundary`  | events on 08-31 and 09-01 → `read_events` over 09-01 returns both (prior day lookback)                                              |
| `test_beat_throttle`                             | two `cmd_beat` within 60 s → one line; after 61 s → two                                                                             |
| `test_beat_outside_roots_writes_nothing`         | cwd `~/Downloads` → no line                                                                                                         |
| `test_parse_kitty_ls_picks_active_window`        | fixture: one OS window, two tabs, `is_focused` false everywhere, second tab active with foreground cwd `<root>/assets.1` → that cwd |
| `test_parse_kitty_ls_at_prompt_uses_window_cwd`  | active window with empty `foreground_processes` → window `cwd`                                                                      |
| `test_parse_kitty_ls_nothing_active`             | no `is_active` at some level → `None`                                                                                               |
| `test_kitty_cwd_missing_socket_is_none`          | `kitty_socket` pointing at a non-existent path under `tmp_path` → `None`                                                            |
| `test_kitty_cwd_live`                            | skipped unless `KITTY_PID` is set and the socket exists → returns an existing directory                                             |
| `test_quarter_hours_sums_to_rounded_total`       | `{a: 1.13, b: 2.21, c: 0.7}` → values multiples of 0.25 summing to `round(4.04*4)/4 = 4.0`                                          |
| `test_invoice_excludes_personal`                 | personal minutes present → not in table                                                                                             |
| `test_render_bar_width_and_cap_marker`           | `NO_COLOR=1`, 10 h over 12 h scale, width 24 → 20 filled cells, marker at cell 16                                                   |
| `test_week_highlights_cap_and_weekend`           | 9 h Tuesday and 1 h Saturday → both labels carry the red escape when color on                                                       |
| `test_import_timew_maps_tags`                    | sample export with `exodus`+`project:x` → work/x; only `exodus` → work/general; neither → personal/general                          |
| `test_import_timew_refuses_twice`                | second call → `SystemExit`, no new lines                                                                                            |
| `test_fix_rejects_end_before_start`              | `15:00 14:00` → `SystemExit`                                                                                                        |

Integration (`test_tagwerk.py::TestCli`, subprocess against the script with env overrides)

- `fix 09:00 10:30 assets` then `today` → table shows `assets 1:30`, total `1:30`
- `fix 09:00 10:00 assets`, `fix 10:00 11:00 auberge --personal`, `invoice <month>` → only `assets 1.00`, total `1.00`
- `beat claude --cwd <tmp work root>/assets.1` → one `beat` line with `src` and `cwd`
- `--help` for every subcommand exits 0

Lint and types: `mise run check && uv run mypy --strict tagwerk.py`.

## 10. E) Commits

Layers: `backend` = `tagwerk.py` + tests, `infra` = `contrib/` and dotfile wiring. No middletier, no frontend. Every commit leaves `mise run check` and `mise run test` green.

Phase 1 — ledger usable by hand (`fix` + `today`)

1. `chore: scaffold module, tests and ci test step`
   - `tagwerk.py` with shebang + `main` + `--help`, `test_tagwerk.py` with `--help` test, `mypy` in the dev group, mise task `test`, CI step `mise run test`, README stub
   - Tests: `--help` exits 0; `mise run test` green locally and in CI
2. `feat(store): append and read monthly jsonl events`
   - `Config` minimal (data_dir), `append_event`, `read_events`, `month_file`
   - Tests: roundtrip, month boundary
3. `feat(config): resolve cwd and window title to kind and project`
   - `load_config`, roots, title rules, `kitty_socket`, lease keys, `resolve_cwd`, `resolve_title`, `config.example.toml`
   - Tests: 8 resolve tests
4. `feat(grid): attribute present minutes to projects with even split`
   - `attribute`
   - Tests: 12 attribute tests
5. `feat(cli): fix and today commands`
   - `cmd_fix`, `cmd_today`, time parsing, table rendering
   - Tests: fix validation, local date bucketing, CLI `fix` + `today`

Phase 2 — sensors

6. `feat(sensor): throttled beat command`
   - `cmd_beat`, stdin JSON, runtime stamp files
   - Tests: throttle, outside roots
7. `feat(sensor): idle and active commands with hypridle listener`
   - `cmd_idle`, `cmd_active`, `contrib/hypridle.conf` (general + listener blocks, section 8)
   - Tests: two lines appended with correct `ev`
8. `feat(sensor): focus poller via hyprctl and kitty remote control`
   - `active_window`, `parse_kitty_ls`, `kitty_cwd`, `cmd_focus`, `contrib/tagwerk-focus.service`
   - Tests: three `parse_kitty_ls` fixture cases, missing socket, live skip
9. `feat(contrib): claude code hooks fragment and pi extension`
   - `contrib/claude-hooks.json` (SessionStart, UserPromptSubmit, PostToolUse `*`, Stop → `tagwerk beat claude`, `timeout: 5` each), `contrib/pi/tagwerk.ts` (`session_start`, `turn_start`, `tool_execution_end`, `agent_settled` → spawn `tagwerk beat pi --cwd process.cwd()`)
   - Tests: JSON fragment parses, every hook command starts with `tagwerk beat claude` and carries `timeout: 5`

Phase 3 — reports

10. `feat(report): week and month bars with caps and weekend highlight`
    - `cmd_week`, `cmd_month`, `render_bar`, `color`
    - Tests: bar width and marker, highlight, `NO_COLOR`
11. `feat(report): invoice markdown with quarter-hour rounding`
    - `cmd_invoice`, `quarter_hours`
    - Tests: rounding sums, personal excluded, CLI invoice

Phase 4 — migration and docs

12. `feat: import timewarrior export as spans`
    - `cmd_import_timew`, idempotency guard
    - Tests: tag mapping, refuse twice
13. `docs: install and rollout guide`
    - README: symlink, config, hypridle, systemd unit, Claude hooks merge, pi symlink, import, cleanup of taskwarrior timers, verification commands; attribution notes (a leased repo is credited while an unrelated window is focused; an abandoned video books `personal/other`)

## 11. F) Closing tasks

- Review the branch as a second engineer would (`/code-review` or `snape-code-reviewer`): correctness of the minute walk at range edges, DST day, empty data, `hyprctl` or `kitten` absent, kitty socket missing.
- Pick which review findings to apply, apply them, rerun tests until green.
- Read every changed file and delete comments that only restate code. Keep `ponytail:` ceilings.

## 12. Rollout checklist (manual, after phase 4)

```
sudo pacman -S hypridle
ln -s ~/code/tagwerk/tagwerk.py ~/.local/bin/tagwerk
install -Dm644 ~/code/tagwerk/config.example.toml ~/.config/tagwerk/config.toml   # then edit roots
install -Dm644 ~/code/tagwerk/contrib/hypridle.conf ~/.config/hypr/hypridle.conf  # omarchy skill
install -Dm644 ~/code/tagwerk/contrib/tagwerk-focus.service ~/.config/systemd/user/tagwerk-focus.service
systemctl --user daemon-reload && systemctl --user enable --now hypridle.service tagwerk-focus.service
chezmoi diff ~/.config/kitty/kitty.conf   # allow_remote_control drift; the system default already covers tagwerk
ln -s ~/code/tagwerk/contrib/pi/tagwerk.ts ~/.pi/agent/extensions/tagwerk.ts
# merge contrib/claude-hooks.json into ~/.claude/settings.json hooks (keep exodus-pretool-hook and rtk)
tagwerk import-timew
```

Verify after 10 minutes: `tail -5 ~/.local/share/tagwerk/$(date +%Y-%m).jsonl` shows `focus` lines with a cwd while a kitty window is focused, and `tagwerk today` shows the current repo.

After a week of trusted numbers:

```
systemctl --user disable --now bugwarrior-pull.timer tw-today-reset.timer
sudo rm /usr/lib/systemd/system-sleep/timew-stop
sudo pacman -Rns timew
```

Taskwarrior itself: see `~/knowledge/areas/dev/task-management-rethink.md`.

## 13. Known ceilings (mark in code with `ponytail:`)

- 15 s poll granularity and a 60 s re-poll. Hyprland socket2 was evaluated and rejected: it cannot see `cd` inside a window and emits a title event per spinner frame.
- Video playback holds an idle inhibitor, so hypridle keeps you present. Chosen: honour inhibitors, so an abandoned video books `personal/other`; flip `ignore_dbus_inhibit = true` in `hypridle.conf` if the chart looks inflated.
- Suffix rule splits on the first `.`; a repo whose name contains a dot (`zk-kit.solidity` in old timew data) would be truncated. Add an explicit root or rename the dir if it ever matters.
- Spans override sensors wholesale for their range; no partial merge.
