# kyrio

Workflow skills for everyday software engineering, packaged as a Claude Code
plugin. The pack is written once and installs unchanged on any machine.

## Install

```
/plugin marketplace add <this repo's url>
/plugin install kyrio@kyrio
/kyrio:setup
```

`/kyrio:setup` probes what this machine can reach, reports it, and installs
nothing. Re-run it whenever the machine gains a new tool or connection.

## How it is put together

Skills describe the work in plain prose and name no product. A local broker,
`kyrio`, is the only component that knows how to reach a system on this
machine, and it reads its configuration from files that live outside this
repository.

See [docs/DESIGN.md](docs/DESIGN.md) for the full design, its invariants, and
the reasoning behind them.

## Requirements

Python 3.12 or newer on `PATH`. No third-party packages.

## Development

Install the blocking portability check once per clone:

```
git config core.hooksPath .githooks
```

It is deliberately not automatic. A repository that points git at its own hook
directory on the strength of a clone has changed how the machine behaves before
anyone read what it runs. CI runs the same check, so a missed hook is caught
before a merge rather than after a push.

The check and the suite, from `plugins/kyrio/`:

```
python scripts/check_portability.py
python -m unittest discover -s tests -t tests
```

Both run on Linux, Windows, and macOS in CI. The pack depends on the standard
library alone, so there is nothing to install first.

The manifests are judged by the plugin CLI rather than by anything in this
repository, so they are checked with the tool that judges them:

```
claude plugin validate . --strict
claude plugin validate plugins/kyrio --strict
```

CI runs both on every push, and `claude plugin tag` runs the same validation
before it will write a tag.
