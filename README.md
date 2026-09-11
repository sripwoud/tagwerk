# tagwerk

[![AUR](https://img.shields.io/aur/version/tagwerk-git?label=AUR&color=1793d1)](https://aur.archlinux.org/packages/tagwerk-git)

Passive work-hours ledger for one Linux desktop running Hyprland, kitty and Omarchy. Zero manual start or stop: the timewarrior setup it replaces died of manual discipline. Two consumers: a monthly invoice and a burnout check.

| Piece        | What it does                                                                                         |
| ------------ | ---------------------------------------------------------------------------------------------------- |
| Focus poller | `tagwerk focus` as a systemd user service polls window class, title and kitty cwd                    |
| hypridle     | `tagwerk idle` and `tagwerk active` from the idle listener and around sleep                          |
| Agent hooks  | Claude Code hooks and a pi extension run `tagwerk beat` with the agent's cwd                         |
| Ledger       | `~/.local/share/tagwerk/YYYY-MM.jsonl`, append-only, UTC timestamps                                  |
| Attribution  | a present minute is split evenly across leased repos, else the ambient bucket, else `personal/other` |
| Reports      | `day`, `week`, `month`, `invoice`; `fix` appends a span                                              |

Vocabulary: `CONTEXT.md`. Decisions with their trade-offs: `docs/adr/`. Spec and tickets: [issue #4](https://github.com/sripwoud/tagwerk/issues/4).

## Install

Target: Omarchy 4 with Hyprland and kitty. The [AUR package](https://aur.archlinux.org/packages/tagwerk-git) pulls `hypridle` and the system Python; tagwerk itself has no runtime dependencies (ADR-0004) and tracks master (ADR-0005).

```sh
paru -S tagwerk-git
tagwerk init && $EDITOR ~/.config/tagwerk/config.toml
systemctl --user enable --now tagwerk-idle.service tagwerk-focus.service
```

Any AUR helper works: `yay -S tagwerk-git`. Without one, `git clone https://aur.archlinux.org/tagwerk-git.git && cd tagwerk-git && makepkg -si` builds the package and installs it through pacman.

In the config, point `[roots]` at your work org's clone directory as `work` and at your personal code directory as `personal`. A fixed-price customer's directory is `fixed`: paid, so it counts toward the caps, but never on the hourly customer's invoice. Make the `[[title]]` patterns match your org's GitHub titles and chat apps. The longest root wins. The project is the first directory below the root, cut at its first dot, so `assets.8467` and `assets` are one project. Every other key ships with its default; the comments in the file `tagwerk init` writes explain each one.

| kind       | counts toward the caps | on the invoice |
| ---------- | :--------------------: | :------------: |
| `work`     |          yes           |      yes       |
| `fixed`    |          yes           |       no       |
| `personal` |           no           |       no       |
| `off`      |           no           |       no       |

A kind never names the payer. `off` belongs to a span, not to a root or a title rule: the config rejects it there, and `tagwerk fix --kind off` books it.

Omarchy's shell already runs the screensaver at 150 s and the lock at 300 s, so `tagwerk-idle.service` runs hypridle on the packaged `/usr/share/tagwerk/hypridle.conf`, which only feeds the ledger. Its listener fires at the same 150 s without input and no minutes fall between screensaver and lock. If you already run `hypridle.service` with your own config, add its `tagwerk idle` and `tagwerk active` lines there and skip `tagwerk-idle.service`.

The poller reads the kitty cwd over kitty remote control on Omarchy's per-pid socket. `/etc/xdg/kitty/kitty.conf` already sets `allow_remote_control socket-only` and `listen_on`; a user `kitty.conf` must not override them. A kitty started outside Omarchy's config has no socket; its cwd is written as `null` and the poller keeps running.

## Agent hooks

Claude Code:

```sh
jq -s '.[1].hooks as $add | .[0] | .hooks = reduce ($add | keys[]) as $k (.hooks // {}; .[$k] = ((.[$k] // []) + $add[$k] | unique))' \
  ~/.claude/settings.json /usr/share/tagwerk/claude-hooks.json > ~/.claude/settings.json.new \
  && mv ~/.claude/settings.json.new ~/.claude/settings.json
```

`claude-hooks.json` adds `SessionStart`, `UserPromptSubmit`, `PostToolUse` and `Stop` hooks with 5 s timeouts. The jq line appends them to the hooks the settings already hold and is safe to rerun. `PostToolUse` is not optional: without it a 20 min agentic turn loses minutes 10 to 20 once the beat lease runs out.

pi:

```sh
ln -s /usr/share/tagwerk/pi/tagwerk.ts ~/.pi/agent/extensions/tagwerk.ts
```

pi runs under Bun with its own `PATH`, so the extension spawns `/usr/bin/tagwerk` by absolute path. Restart pi to load it.

## Verify

After 10 minutes with a kitty window focused for part of them:

```sh
systemctl --user status tagwerk-idle.service tagwerk-focus.service
tail -n 5 ~/.local/share/tagwerk/$(date -u +%Y-%m).jsonl
tagwerk day
```

Both units should be `active (running)`. The `focus` lines carry a path in `cwd` while kitty was focused and `null` otherwise, and `day` lists the repo you were in. Leave the machine for three minutes: an `idle` line appears, then `active` when you come back. Poller errors go to `journalctl --user -u tagwerk-focus.service`; systemd restarts it after 5 s.

## Migrating from timewarrior

```sh
tagwerk import-timew --work-tag TAG
```

`TAG` is the timewarrior tag that marked an interval as work (`timew tags` lists them); intervals without it become `personal`. `project:<repo>` tags become the project, anything else lands in `general`. The import reads `timew export` and refuses to run twice, so run it before uninstalling timew.

After a week of trusted numbers, retire the timewarrior setup:

```sh
systemctl --user disable --now bugwarrior-pull.timer tw-today-reset.timer
sudo rm /usr/lib/systemd/system-sleep/timew-stop
omarchy pkg drop timew
```

The sleep hook belongs to no package and runs `timew stop` on every suspend. Timewarrior's intervals are already in the ledger as spans. Taskwarrior stays.

## Commands

Times are local; `HH:MM` means today. Every report takes an optional period in its own unit, or `--ago N` counted in that same unit. The two are mutually exclusive. With neither, the report covers the current period.

| Command                                       | Does                                                                                                                         |
| --------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `tagwerk`                                     | the description, two examples and where to go next; no command is not an error                                               |
| `tagwerk init`                                | write the commented config template to `~/.config/tagwerk/config.toml`; refuses to overwrite                                 |
| `tagwerk day [YYYY-MM-DD]`                    | hours per `kind/project` for the local day, the paid `work` subtotal, total                                                  |
| `tagwerk week [YYYY-Www]`                     | one bar per day, Monday to Sunday; cap marker, red over the day cap or on a weekend with minutes                             |
| `tagwerk month [YYYY-MM]`                     | one bar per ISO week, then hours per `kind/project`                                                                          |
| `tagwerk invoice [YYYY-MM]`                   | markdown table of `work` hours per project in quarter hours; rows sum to the rounded total; `fixed` never appears            |
| `tagwerk fix START END PROJECT [--kind KIND]` | book a span that overrides the sensors for its range; `KIND` is `work` (default), `fixed`, `personal` or `off`               |
| `tagwerk import-timew --work-tag TAG [FILE]`  | one-shot import of the timewarrior export as spans; runs `timew export` when `FILE` is omitted                               |
| `tagwerk focus [--once]`                      | the poller; `--once` writes one poll and exits                                                                               |
| `tagwerk beat SRC [--cwd PATH]`               | an agent signal from `SRC` (`claude` or `pi`); cwd from `--cwd`, else the `cwd` field of JSON on stdin, else the process cwd |
| `tagwerk idle`, `tagwerk active`              | idle marks, written by hypridle                                                                                              |
| `tagwerk --version`                           | the git revision the package was built from, or `master` from a checkout (ADR-0007)                                          |
| `tagwerk --config PATH CMD`                   | read this config; beats `TAGWERK_CONFIG`, which beats `~/.config/tagwerk/config.toml`                                        |
| `tagwerk --data-dir PATH CMD`                 | read and write this ledger directory; beats `TAGWERK_DATA_DIR`, which beats `data_dir` in the config; `init` rejects it      |

```sh
tagwerk fix 14:00 15:00 assets
tagwerk fix 09:00 12:00 auberge --kind fixed
tagwerk fix 2026-09-08T09:00 2026-09-08T10:00 blog --kind personal
tagwerk fix 12:00 13:00 lunch --kind off
tagwerk week --ago 1
tagwerk invoice 2026-08
tagwerk --config ~/side-gig/tagwerk.toml invoice 2026-08
```

Both flags go before the subcommand. Reports colour only when both stdout and stderr are terminals; `--no-color`, `NO_COLOR` and `TERM=dumb` each turn it off, and `FORCE_COLOR` overrides all three.

## Attribution notes

- A minute is present when you are not idle and a poll landed within the last 2 minutes. Present minutes are credited exactly once, so daily totals equal wall-clock presence.
- An agent beat leases its repo for 10 minutes; a focused kitty cwd or a GitHub repo title leases for 1 minute. A leased repo is credited while an unrelated window is focused, such as a browser tab during a long Claude turn. Two leased repos split each minute evenly (ADR-0003).
- Beats while idle book nothing. An unattended overnight agent adds no hours; credit resumes on the still-valid lease when you return.
- With no lease the focused window decides. A kitty shell sitting at a root books that kind's `general`; a work-pattern title (Slack, Zoom, Meet, your org) books `work/general`; anything else books `personal/other`.
- Idle inhibitors are honoured, so a video call with your hands off the keyboard stays present. In exchange an abandoned video also stays present and books `personal/other`; that inflates the chart, never the invoice. If the chart looks inflated, copy `/usr/share/tagwerk/hypridle.conf`, set `ignore_dbus_inhibit = true` in the copy, and point hypridle's `--config` at it with `systemctl --user edit tagwerk-idle.service`.
- `work` and `fixed` are both paid: both drive the day and week caps and both land in the `work` subtotal. Only `work` reaches `tagwerk invoice`, so fixed-price hours show up in the burnout check and never on an hourly customer's bill (ADR-0006).
- A span overrides the sensors for its whole range, no partial merge, and the latest appended span wins on overlap. Nothing in the ledger is ever edited (ADR-0002).
- A renamed repo keeps one row in every report: add `"old-name" = "new-name"` under `[rename]` and the old name folds into the new one for all time, past months included. The kind is never rewritten, so minutes credited as `personal` stay personal even if the project now sits under a work root (ADR-0010).

## Known ceilings

- Poll granularity is 15 s with a 60 s re-poll. Hyprland's event socket was rejected: it cannot see `cd` inside a terminal and emits a title event per spinner frame.
- A repo whose name contains a dot is truncated at it; give it its own root, or rename the directory and add a `[rename]` entry so the old minutes follow.
- A directory that holds worktrees books every worktree under it to the container's name, because the project is the first directory below the root. Give the container its own root: the longest match then picks it, and the repo name below it becomes the project.
- A rename keys on the project name, not the path, so one entry reaches spans and window titles as well as cwds. In exchange two repos sharing a name under different roots fold together, and a rename is single-hop: renaming twice means pointing both old names at the current one.
- Colours come from five hues that pass the contrast check; past a handful of work repos two will share one.
- Spans written before `ts` carried microseconds resolve by file order when two of them share a second across month files; their append order was never recorded, so no rewrite can fix it (ADR-0009).
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

`contrib/aur/` holds the PKGBUILD. A push to master that touches it publishes `tagwerk-git` to the AUR; code changes reach users through `paru -Syu --devel` with no publish. The package builds master, so a renamed `contrib` file and its PKGBUILD line ship in the same PR. `makepkg -f --nodeps` inside `contrib/aur` builds it locally.
