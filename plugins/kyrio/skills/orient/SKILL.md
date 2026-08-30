---
name: orient
description: >
  Get oriented in a repository you do not know: what it is, what runs it, how
  it is built and tested, which parts are alive, and who to ask. Breadth first,
  the shape of the whole with named paths, before any detail. Use it on a
  codebase you have not worked in, on returning to one after months, or before
  estimating work in one. It reads only and changes nothing. Not for following
  one behaviour down through the layers — that is depth-first work, and
  `/kyrio:trace` — and not for reviewing a change, a diff, or uncommitted work.
allowed-tools: Bash(kyrio:*), Read, Grep
---

# Orient

Produce a picture of the whole repository that a person can hold in their head:
one screen, named paths, no detail that does not change the picture.

Run from inside the repository. If the working directory is somewhere else, ask
for the path before starting.

## 1. Shape

```sh
kyrio repo map
```

Returns the root, the branch, the tracked file count, detected build and test
commands, the top-level directories with what each mostly holds, and the entry
points it found.

Two responses change what happens next:

- **Not a repository** — stop and say so. Everything below reads history.
- **Build or test not detected** — leave them unknown in the report and name
  where to look. Do not infer a command from a filename. A test command that is
  wrong costs more than one that is missing.
- **Several independent products at the top level** — ask which one before
  going further. One report covering all of them fits none of them.

## 2. Where the work is

```sh
kyrio repo churn --since 90d --top 20
```

Returns the files that changed most and how often, over a date range the
response states explicitly.

Churn is fact, not verdict. A file at the top of the list is either the healthy
core or the part nobody has got right yet, and the count alone does not say
which. Name the files; do not grade them.

Change the window when the default says little — a shorter one where traffic is
heavy, a longer one where the repository is quiet. State the window you used.

## 3. Who to ask

For each of the two or three directories that dominate the churn list:

```sh
kyrio repo owners <path>
```

The response says whether it read an ownership file or fell back to the most
frequent committers. Carry that difference into the report: a declared owner is
a commitment, a frequent committer is a lead.

## 4. Check the map against the code

The map is built from filenames and manifests. Confirm it before repeating it.

Read between two and four files, chosen from the entry points the map named and
the top of the churn list. For each, establish what happens when execution
starts there, and what it reaches next.

Where the reading and the map disagree, the reading wins, and the report says
which claim was corrected.

Stop at four files. Past that it is depth-first work, and a different job.

## 5. Report

One screen, in this order:

1. **What this is** — two or three sentences, the kind a person could repeat to
   someone else.
2. **Build and test** — the exact commands, or `not detected` and where to look.
3. **Layout** — top-level directories, one line each.
4. **Entry points** — named paths, with what starts there.
5. **Active areas** — the churn list narrowed to what matters, with the window.
6. **Who to ask** — per area, and whether declared or inferred.
7. **Open questions** — what is still unknown, phrased so it can be asked.

Mark each claim as observed or inferred. Inferred claims are worth making;
inferred claims presented as observed are how an orientation becomes wrong
without anyone noticing.

## Bounds

- Name paths, never layers: `src/queue/worker.py:40`, not "the worker layer".
- Do not open twenty files. The map and the churn list choose the few.
- Do not say what the code should have been. This is orientation, not review.
- Write nothing, and change nothing.
- Where the history is one commit deep, say so: churn and ownership have
  nothing to report, and the map carries the whole answer.
