# tagwerk

Passive work-hours ledger for one Linux desktop. Sensors append events, attribution credits every present minute to a project, and reports render the totals for a monthly invoice and a burnout check.

## Language

### Ledger and sensors

**Ledger**:
The append-only monthly JSONL event store. Nothing in it is ever rewritten.
_Avoid_: store, log, database

**Sensor**:
Anything that appends events: the idle listener, the focus poller, the agent hooks.
_Avoid_: watcher, collector, tracker

**Poll**:
A `focus` event written by the focus poller. A recent poll is what proves the machine was on.
_Avoid_: heartbeat, tick, pulse

**Beat**:
A `beat` event written by an agent hook (Claude Code, pi) carrying the agent's cwd.
_Avoid_: heartbeat, ping

**Span**:
A manually entered or imported interval that overrides the sensors for its whole range. The latest appended span wins on overlap.
_Avoid_: interval, entry, correction

### Attribution

**Root**:
A configured path prefix that maps everything beneath it to one kind. The longest matching root wins.
_Avoid_: workspace, base dir

**Kind**:
One of `work`, `personal`, `off`. Work is invoiced, personal is charted only, off is time removed entirely.
_Avoid_: category, type, tag

**Project**:
The unit minutes are attributed to: a repo or a catch-all.
_Avoid_: client, task, tag

**Repo**:
A project named after a repository, derived from a cwd under a root with any worktree suffix stripped, or from a GitHub title. Only repos take leases.
_Avoid_: repository, named project

**Catch-all**:
The two projects that are not repos: `general` (inside a kind's territory with no repo, such as a shell sitting at a root or a work-pattern window title) and `other` (no recognisable signal at all).
_Avoid_: fallback, misc, unknown

**Bucket**:
A `(kind, project)` pair that minutes are credited to.
_Avoid_: account, line, category

**Idle**:
The state between an `idle` event and the next `active` event from the idle listener. Suspend is idle.
_Avoid_: away, AFK

**Absent**:
No poll recently enough to prove the machine was on: powered off, suspended, or the poller is dead.
_Avoid_: offline, gap

**Present**:
A minute in which the user is at the machine: neither idle nor absent.
_Avoid_: active, online, worked

**Credited**:
A minute booked to a bucket: every present minute, plus every minute covered by a span of kind work or personal. Off spans and non-present minutes are never credited.
_Avoid_: worked, logged, tracked

**Lease**:
The period after a repo signal (a beat, a focused kitty cwd, a GitHub title) during which that repo is eligible for credit. A present minute is split evenly across all leased repos.
_Avoid_: window, TTL, timeout

**Ambient bucket**:
The bucket implied by the focused window when it is not a repo, such as `work/general` while Slack is focused. Credited only when no repo holds a lease.
_Avoid_: context, fallback bucket

### Reports

**Cap**:
A daily or weekly hours threshold above which reports highlight the period. Caps change colours, never numbers.
_Avoid_: limit, quota, budget

**Invoice**:
The monthly table of work hours per project, rounded to quarter hours so the rows sum to the rounded total.
_Avoid_: bill, timesheet
