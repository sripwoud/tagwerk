# Flags instead of positionals for `fix`

`tagwerk fix START END PROJECT [--kind KIND]` keeps its three positional arguments. It will not be
reshaped into `fix --from START --to END --project PROJECT`.

## Why this is out of scope

clig.dev says two or more arguments for _different_ things is problematic, and on its face `fix` is
the textbook violation: three positionals, three unrelated meanings. The concrete harm usually cited
for that rule is silent misordering — you swap two arguments and the command cheerfully does the
wrong thing.

That harm does not exist here. Both time slots use `parse_local` as their argparse `type=` callable,
and it raises on anything that isn't `HH:MM` or an ISO datetime. So the types don't overlap, and
every misordering is caught loudly:

```
$ tagwerk fix 09:00 assets 17:00
tagwerk fix: error: argument end: invalid parse_local value: 'assets'

$ tagwerk fix assets 09:00 17:00
tagwerk fix: error: argument start: invalid parse_local value: 'assets'
```

The one misordering that gets past the parser — swapping start and end — hits the `end must be after
start` guard in `cmd_fix` and exits non-zero. There is no input that books a project named `17:00`.

What remains is a readability preference, and it is not free. `fix` is the most-typed write command
in a tool whose entire premise is staying out of the way:

```sh
tagwerk fix 12:00 13:00 lunch --kind off
tagwerk fix --from 12:00 --to 13:00 --project lunch --kind off
```

That is 2.3x the keystrokes, several times a week, forever, to satisfy a style rule whose safety
rationale does not apply. The argument ordering is also fixed, documented in the README table, and
reinforced by 29 tests — it is not a shape anyone has to guess at.

A middle option was considered and rejected: collapsing the range into one positional,
`fix 09:00-17:00 assets`. The ISO form makes it unparseable, since
`2026-09-08T09:00-2026-09-08T10:00` has no unambiguous split point.

## What would reopen this

A second argument slot whose type overlaps with another — for example a project name that parses as
a time, or a new optional positional after `PROJECT`. At that point the types stop enforcing the
order and the silent-failure argument becomes real.

## Prior requests

- #35: "refactor(cli)!: fix takes flags, not three positionals"
