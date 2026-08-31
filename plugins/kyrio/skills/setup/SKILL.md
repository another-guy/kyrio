---
name: setup
description: >
  Prepare this machine to use the kyrio workflow pack, and re-check it later.
  Reports what is present, records what the launcher needs, and proposes the
  one permission rule the pack depends on. Run it after installing or updating
  the plugin, and again whenever the machine gains a tool or a connection.
  It never installs software and never starts a sign-in flow — where something
  is missing it prints the exact command and stops. Not for configuring a
  single repository; that is an ordinary config file, not this skill.
allowed-tools: Bash(kyrio:*), Read
disable-model-invocation: true
---

# Setup

Four steps. Show the user each result before moving to the next.

## 1. Report

```sh
kyrio probe
```

Show the report as it came back. It lists the interpreter, every capability
and its transport, the servers this machine can reach and the state of each,
and which files have been written.

If no interpreter was found, stop here. Give the user the message verbatim,
say that nothing was written, and end. Do not attempt an install.

## 2. Record

If the report says the interpreter is not written yet, or names a different
one from the one now found:

```sh
kyrio probe record
```

This writes two files, and prints both. It replaces only the keys it owns, so
anything hand-edited survives. Safe to run repeatedly.

If the report already shows the correct interpreter recorded, skip this and
say so.

## 3. Capabilities

The SERVERS section says which servers exist and which are connected. Which
one serves which capability is a judgment: the listing proves a server is
reachable, never what it is for.

For each capability still unconfigured, decide whether a connected server
serves it, using its name and address as the only evidence. Where nothing
connected plausibly serves one, leave it alone. An unconfigured capability is
a gap and says so on every report; a wrong mapping is worse than a gap,
because every later call goes somewhere wrong and the report calls it fine.

Show the proposal as one line per capability, each naming the server it came
from and how confident you are. Mark guesses as guesses. Then ask, and change
nothing until the user answers.

Only on a clear yes:

```sh
kyrio probe record --set <capability>=server:<prefix> --servers
```

Repeat `--set` once per capability. `--servers` caches what was seen, so a
later report can say when this machine was last checked.

Where the user says a capability is deliberately not wanted here:

```sh
kyrio probe record --set <capability>=unavailable
```

That is a value rather than an absence. Layers merge, so leaving a key out
never means off.

Assignments are validated before anything is written, and a refusal names its
reason. Do not work around one; show it and ask.

## 4. Permission

```sh
kyrio probe permission
```

If the rule is already present, say so and finish.

Otherwise show the proposed rule and **ask the user before applying it**. State
plainly what it grants: every `kyrio` call, and nothing else, because the
broker runs no command supplied by its caller. Only on a clear yes:

```sh
kyrio probe permission --apply
```

If the user declines, say what the cost is — a permission prompt on each call
in each new turn — and finish without applying. Do not ask a second time.

## Finishing

Close with what the machine can do now and what it cannot yet. Anything shown
as not configured is a gap to be filled later, not an error. Say which
capabilities are ready and name the skills that work today.

Do not edit `settings.json`, the config file, or any file in the working
directory by hand. Every write belongs to a command above, so that re-running
this skill produces the same result.
