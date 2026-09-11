# `--version` is the git revision the package was built from

`VERSION` is a module constant holding `master`, and `package()` seds `$pkgver` over it, so an installed `tagwerk --version` prints `tagwerk r18.06781b3` and a run from a checkout prints `tagwerk master`. The installed copy names the exact commit it was built from; the checkout names only that it is unstamped, which is all an unstamped copy can honestly claim.

Rejected: a semver constant bumped by CI, which invents a release process ADR-0005 deliberately does not have, and would drift the moment a commit landed without the bump. Rejected: reading the version at runtime from package metadata, which ADR-0004 forbids — there is no package, only `/usr/bin/tagwerk`. Rejected: `master` alone, which answers nothing a bug report does not already know from the package name. The `version` in `pyproject.toml` is metadata for the dev venv and names nothing the user runs.

Consequence: the constant's exact spelling is load-bearing for the PKGBUILD, so a test applies the PKGBUILD's own sed line to the script and fails if the two drift. `pkgver` changes on every commit, so every build reports a distinct version with no bookkeeping.
