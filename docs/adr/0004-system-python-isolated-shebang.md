# Shebang `#!/usr/bin/python3 -I`, not `env python3`

The tool runs from hooks fired inside arbitrary repos, and the mise shim `~/.local/share/mise/shims/python3` precedes `/usr/bin` on PATH. `env python3` inside a worktree with a pinned Python would run the wrong interpreter and fail silently, the same failure that crashed pi via a repo `.nvmrc`. `/usr/bin/python3` is the Arch system Python; `-I` additionally ignores `PYTHONPATH`, `PYTHON*` and user site-packages.

Consequence: stdlib only, no runtime dependencies, ever.
