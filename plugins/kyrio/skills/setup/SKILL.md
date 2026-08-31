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

Two passes: propose what the machine shows, then ask about what it cannot.

### Pass 1 — what the machine shows

The SERVERS section lists what is reachable, each with a derived `PREFIX`.
Reachable is not the same as relevant: the listing proves a server answers,
never what it is for.

Weigh the evidence, not the presence. Something can be installed by mistake,
bundled by a policy, or left over from a trial — so what counts is a sign that
a person chose it here:

- `connected` — somebody signed in on purpose. Strong enough to propose.
- `needs auth`, `pending`, `unreachable` — ambiguous. Do not propose. Carry it
  into pass 2 and say what was seen.
- `unknown` — a state this version does not recognize. Repeat it verbatim and
  carry it into pass 2.

Use `PREFIX` exactly as given; never construct one.

Show one line per proposal: the capability, the server it came from, and how
confident you are. Mark guesses as guesses. Ask, and change nothing until the
user answers. Only on a clear yes:

```sh
kyrio probe record --set <capability>=server:<prefix> --servers
```

Repeat `--set` once per capability. `--servers` caches what was seen, so a
later report can say when this machine was last checked.

### Pass 2 — what the machine cannot show

Everything still unconfigured. The machine holds no evidence about these and
the answer is in the user's head, so it has to be asked for. Not asking is the
real failure here: a capability nobody was asked about stays a gap forever,
and the report never explains why.

Ask about all of them in one message, not one at a time. Name what each
capability is for in a few words, mention anything pass 1 saw but did not
trust, and offer four answers:

1. **one of the servers above** — record `server:<prefix>`
2. **a command-line tool** — record `cli:<what the user calls it>`
3. **not used here** — record `unavailable`, and it stops being asked about
4. **not sure, or later** — record nothing; the next run asks again

Three and four are different answers and must stay different. One is a
decision, the other is an unanswered question.

Record the user's own word for a tool. Do not invent an identifier and do not
correct their spelling; the broker canonicalizes it.

Where nothing here can serve what they name, record it anyway and say so
plainly: the entry keeps the decision, the report then names the missing piece
instead of blaming their machine, and it starts working the day that piece
ships. Until then it reads as configured and not usable, which is the truth.

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
