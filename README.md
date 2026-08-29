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
