# tagwerk

Passive work-hours ledger for one Linux desktop running Hyprland, kitty and Omarchy. Sensors that already exist on the machine append events to a monthly JSONL ledger. Attribution credits every present minute exactly once. Reports render the totals for a monthly invoice and a burnout check. Nothing is started or stopped by hand.

| Piece        | What it does                                                                                         |
| ------------ | ---------------------------------------------------------------------------------------------------- |
| Focus poller | `tagwerk focus` as a systemd user service: window class, title and kitty cwd every 15 s              |
| hypridle     | `tagwerk idle` and `tagwerk active` after 150 s without input and around sleep                       |
| Agent hooks  | Claude Code hooks and a pi extension run `tagwerk beat` with the agent's cwd                         |
| Ledger       | `~/.local/share/tagwerk/YYYY-MM.jsonl`, append-only, UTC timestamps                                  |
| Attribution  | a present minute is split evenly across leased repos, else the ambient bucket, else `personal/other` |
| Reports      | `today`, `week`, `month`, `invoice`; corrections are spans appended with `fix`                       |

Vocabulary: `CONTEXT.md`. Decisions with their trade-offs: `docs/adr/`. Spec and tickets: [issue #4](https://github.com/sripwoud/tagwerk/issues/4).

## Install

Target: Omarchy 4 with Hyprland and kitty, the Arch system Python 3.13 or later, `hypridle` from `extra`. No runtime dependencies (ADR-0004), so the install is a symlink. `~/.local/bin` is on the user services' `PATH`, so hypridle calls `tagwerk` by name.

```sh
omarchy pkg add hypridle
git clone https://github.com/sripwoud/tagwerk ~/code/tagwerk
ln -s ~/code/tagwerk/tagwerk.py ~/.local/bin/tagwerk
install -Dm644 ~/code/tagwerk/config.example.toml ~/.config/tagwerk/config.toml
$EDITOR ~/.config/tagwerk/config.toml
install -Dm644 ~/code/tagwerk/contrib/hypridle.conf ~/.config/hypr/hypridle.conf
install -Dm644 ~/code/tagwerk/contrib/tagwerk-focus.service ~/.config/systemd/user/tagwerk-focus.service
systemctl --user daemon-reload
systemctl --user enable --now hypridle.service tagwerk-focus.service
chezmoi diff ~/.config/kitty/kitty.conf
ln -s ~/code/tagwerk/contrib/pi/tagwerk.ts ~/.pi/agent/extensions/tagwerk.ts
jq -s '.[1].hooks as $add | .[0] | .hooks = reduce ($add | keys[]) as $k (.hooks // {}; .[$k] += $add[$k])' \
  ~/.claude/settings.json ~/code/tagwerk/contrib/claude-hooks.json > ~/.claude/settings.json.new \
  && mv ~/.claude/settings.json.new ~/.claude/settings.json
tagwerk import-timew --work-tag TAG
```

- **Config**: point `[roots]` at your work org's clone directory as `work` and your personal code directory as `personal`, and make the `[[title]]` patterns match your org's GitHub titles and chat apps. The longest root wins. The project is the first directory below the root, cut at its first dot, so `assets.8467` and `assets` are one project. Every other key ships with its default; the comments in `config.example.toml` explain each one.
- **hypridle**: Omarchy's shell already runs the screensaver at 150 s and the lock at 300 s; `hypridle.conf` only feeds the ledger. Its listener fires at the same 150 s, so no minutes fall between screensaver and lock. The package ships `hypridle.service`, bound to the graphical session.
- **kitty**: the poller reads the cwd over kitty remote control on Omarchy's per-pid socket. `/etc/xdg/kitty/kitty.conf` already sets `allow_remote_control socket-only` and `listen_on`, so a user `kitty.conf` needs neither. If `chezmoi diff` shows an `allow_remote_control` line that differs between source and target, make them agree: `chezmoi add ~/.config/kitty/kitty.conf` keeps the live file, `chezmoi apply ~/.config/kitty/kitty.conf` keeps the source. Either works for tagwerk. A kitty started outside Omarchy's config has no socket; its cwd is written as `null` and the poller keeps running.
- **Claude Code**: the fragment adds `SessionStart`, `UserPromptSubmit`, `PostToolUse` and `Stop` hooks with 5 s timeouts. The jq line appends them to the hooks the settings already hold. `PostToolUse` is not optional: without it a 20 min agentic turn loses minutes 10 to 20 once the beat lease runs out.
- **pi**: pi runs under Bun with its own `PATH`, so the extension spawns `~/.local/bin/tagwerk` by absolute path. Restart pi to load it.
- **Import**: `TAG` is the timewarrior tag that marked an interval as work (`timew tags` lists them); intervals without it become `personal`. `project:<repo>` tags become the project, anything else lands in `general`. The import reads `timew export` and refuses to run twice, so run it before uninstalling timew.

`~/.config/hypr`, `~/.config/kitty`, `~/.config/systemd/user` and `~/.claude/settings.json` are chezmoi-managed on the reference machine; `chezmoi add` each file you changed.

## Verify

After 10 minutes with a kitty window focused for part of them:

```sh
systemctl --user status hypridle.service tagwerk-focus.service
tail -n 5 ~/.local/share/tagwerk/$(date -u +%Y-%m).jsonl
tagwerk today
```

Expected: both units `active (running)`; `focus` lines whose `cwd` is a path while kitty was focused and `null` otherwise; `today` lists the repo you were in. Leave the machine for three minutes and an `idle` line appears, then `active` when you return. Poller errors go to `journalctl --user -u tagwerk-focus.service`; systemd restarts it after 5 s.

## Cleanup

After a week of trusted numbers, retire the timewarrior setup:

```sh
systemctl --user disable --now bugwarrior-pull.timer tw-today-reset.timer
sudo rm /usr/lib/systemd/system-sleep/timew-stop
omarchy pkg drop timew
```

The sleep hook belongs to no package and runs `timew stop` on every suspend. Timewarrior's intervals are already in the ledger as spans. Taskwarrior stays.

## Commands

Every subcommand has `--help`. Times are local; `HH:MM` means today.

| Command                                             | Does                                                                                                                         |
| --------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `tagwerk today`                                     | hours per project for the local day, work subtotal, total                                                                    |
| `tagwerk week [-n N]`                               | one bar per day, Monday to Sunday, N weeks back; cap marker, red over the day cap or on a weekend with minutes               |
| `tagwerk month [YYYY-MM]`                           | one bar per ISO week, then hours per project; default the current month                                                      |
| `tagwerk invoice YYYY-MM`                           | markdown table of work hours per project in quarter hours; rows sum to the rounded total                                     |
| `tagwerk fix START END PROJECT [--personal\|--off]` | book a span that overrides the sensors for its range; `--personal` charts without invoicing, `--off` removes it              |
| `tagwerk import-timew --work-tag TAG [FILE]`        | one-shot import of the timewarrior export as spans; runs `timew export` when `FILE` is omitted                               |
| `tagwerk focus [--once]`                            | the poller; `--once` writes one poll and exits                                                                               |
| `tagwerk beat SRC [--cwd PATH]`                     | an agent signal from `SRC` (`claude` or `pi`); cwd from `--cwd`, else the `cwd` field of JSON on stdin, else the process cwd |
| `tagwerk idle`, `tagwerk active`                    | idle marks, written by hypridle                                                                                              |

```sh
tagwerk fix 14:00 15:00 assets
tagwerk fix 2026-09-08T09:00 2026-09-08T10:00 blog --personal
tagwerk fix 12:00 13:00 lunch --off
tagwerk week -n 1
tagwerk invoice 2026-08
```

`TAGWERK_CONFIG` and `TAGWERK_DATA_DIR` override the config path and the data directory. `NO_COLOR` disables colour, `FORCE_COLOR` forces it; colour is off when stdout is not a terminal.

## Attribution notes

- A minute is present when you are not idle and a poll landed within the last 2 minutes. Present minutes are credited exactly once, so daily totals equal wall-clock presence.
- An agent beat leases its repo for 10 minutes; a focused kitty cwd or a GitHub repo title leases for 1 minute. A leased repo is credited while an unrelated window is focused, such as a browser tab during a long Claude turn. Two leased repos split each minute evenly (ADR-0003).
- Beats while idle book nothing. An unattended overnight agent adds no hours; credit resumes on the still-valid lease when you return.
- With no lease the focused window decides. A kitty shell sitting at a root books that kind's `general`; a work-pattern title (Slack, Zoom, Meet, your org) books `work/general`; anything else books `personal/other`.
- Idle inhibitors are honoured. A video call with your hands off the keyboard stays present. An abandoned video also stays present and books `personal/other`, which inflates the chart, never the invoice. Set `ignore_dbus_inhibit = true` in `~/.config/hypr/hypridle.conf` if the chart looks inflated.
- A span overrides the sensors wholesale for its range; the latest appended span wins on overlap. Corrections never edit the ledger (ADR-0002).

## Known ceilings

- Poll granularity is 15 s with a 60 s re-poll. Hyprland's event socket was rejected: it cannot see `cd` inside a terminal and emits a title event per spinner frame.
- The project name ends at the first dot. A repo whose name contains a dot is truncated; give it its own root or rename the directory.
- Spans replace sensor minutes inside their range; there is no partial merge.
- Colours come from five hues that pass the contrast check; past a handful of work repos two will share one.
- Reports rescan the month files on every run; a month is about 43k minutes and a few thousand events, fine for years of data.
- One machine, no web UI, no sync, no `--json`, no notifications.

## Development

```sh
mise install
hk install --mise
mise run check
mise run test
```

`check` runs dprint and ruff; `test` runs pytest, then mypy strict. Tests drive the CLI with `TAGWERK_CONFIG` and `TAGWERK_DATA_DIR` pointed at a temp directory and fake `hyprctl` and `kitten` executables on `PATH`; they never touch the real ledger.
