# Status

Where the build has actually got to. The plan lives in
[docs/DESIGN.md](docs/DESIGN.md) section 10; this file says which parts of it
are done.

**Built and validated are separate columns on purpose.** Code that is written,
committed, and released can still have never run anywhere but the machine that
wrote it. Saying so is the point of this file.

Current version: **0.3.0**

## Phases

Every phase in [docs/DESIGN.md](docs/DESIGN.md) section 10 is listed, built or
not. A version in *italics* is the planned one from the version plan, not a
release that exists.

| Phase | Feature | Built | Validated | Version | Notes |
|---|---|---|---|---|---|
| **P0** | broker skeleton | yes | yes | 0.1.0 | Cascade resolver, `emit`, `ingest`, `repo`, shims, lint, CI. Suite and lint run on Linux, Windows, and macOS |
| **P1** | orient & trace | yes | yes | 0.2.0 | Zero transports, zero configuration. Tagged `kyrio--v0.2.0`, and ported once — see below |
| **P2** | code review | yes | **partly** | 0.3.0 | `setup` probing, `scm`, `review`, `review-pass`. [validation/P2.md](validation/P2.md). Not tagged |
| **P3** | second machine | n/a | **no** | *1.0.0* | The port. [validation/P3.md](validation/P3.md). Needs a machine that has never had the plugin |
| **P4** | archeology | no | — | *1.1.0* | `issue` and `kb` capabilities, `/kyrio:archeology`. Blocked: P3 is not reorderable |
| **P5** | incident response | no | — | *1.2.0* | `obs` capability, `/kyrio:incident` |
| **P6** | release prep | no | — | *1.3.0* | `ci` capability, `repo release-notes`, `/kyrio:ship` |
| **P7** | everyday skills | no | — | *1.4.0*+ | `standup`, `bugfix`, `feature`, `e2e`, `design`, and `ticket` if D6 is taken. One minor per skill, on demand |

P3 carries the jump to `1.0.0`. Everything before it is `0.x` on purpose: the
machine that wrote an adapter is the worst possible place to find out whether
that adapter leaked.

## What is unfinished

**P2 validation stopped early.** The macOS pass was interrupted by a `--cwd`
bug, fixed in `bdb5abe`. Three checks have still never run, and they are the
three whose failures are invisible from the output alone:

- `/kyrio:review` — whether `context: fork` actually forks. The frontmatter
  validates, which only proves it was not rejected.
- `scm pr comment --post` — the two-call sequence was built from
  documentation, not from watching it work.
- `scm pr diff` counts — `files`, `added`, `removed` are parsed here, not
  reported by the tool. If the tool returns anything but a raw unified diff,
  the payload still looks correct and the counts are wrong.

**0.3.0 is not tagged.** `kyrio--v0.2.0` exists; `kyrio--v0.3.0` does not.
Nothing depends on it — plugin installs key on the commit, not the version —
but the release is unmarked.

**The second `scm` adapter has never run.** `azure-devops` is written and
tested against hand-written fixtures. No part of it has been near a real `az`.
Everything else it does is built on the output shape of `az repos pr show`, so
that is the first thing to try.

**The `manifests` CI job is unproven.** It runs `claude plugin validate
--strict` on a runner with no signed-in account. Whether that works is not yet
known.

## About P3

A port already happened once, at 0.2.0 — the commit message says "P1 shipped
and ported". That was two skills and no transports. Where it ran and what it
found is not recorded anywhere, which is part of why this file exists.

P3 as specified is a different exercise: a machine that has **never** had the
plugin, running everything P1 and P2 shipped, with a capability deliberately
served by a different transport than the first machine uses. The acceptance
criterion is in [validation/P3.md](validation/P3.md).

The macOS machine can no longer test the cold path — it has been set up
already.

## What ships today

- **Skills** — `orient`, `trace`, `setup`, `review`, `review-pass`
- **Capabilities** — `repo` (local), `scm`
- **`scm` adapters** — `github` (reads and writes), `azure-devops` (reads
  only; that host has no comment verb)
- **Checks** — 431 tests, portability lint over 57 files, both manifests
  validated `--strict`

## Keeping this file true

Update it in the same commit as the work it describes. A status file that is
updated afterwards is a status file nobody trusts, and one nobody trusts is
worse than none, because it gets quoted anyway.

Move a phase to **validated** only when its checklist in `validation/` has a
filled-in result row. "It worked when I tried it" is what this file exists to
stop being the record.
