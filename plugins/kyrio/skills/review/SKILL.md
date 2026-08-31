---
name: review
description: >
  Review a change that already exists — a pull request, merge request, or
  whatever this machine calls one — and optionally leave comments on it.
  Fetches the change, reads what it touches, takes a second opinion from a
  reviewer with no memory of this conversation, then drafts comments and posts
  only what you confirm. Use it on somebody else's change or your own once it
  is up. Not for reviewing uncommitted local work, and not for writing the
  change or fixing what it finds.
allowed-tools: Bash(kyrio:*), Read, Grep, Write
---

# Review

One change, two readings, nothing posted without a yes.

## 1. Pin the change

You need an identifier. If the request does not carry one, ask; do not guess
from the branch you happen to be on.

Run from inside the repository the change belongs to. The broker works out
which repository from the working directory, so a diff fetched from the wrong
one is not an error — it is a different change.

## 2. Fetch it

```sh
kyrio scm pr diff <id>
```

The header says how many files and lines moved. Read it before the payload:
that number decides whether this is one review or several.

Three responses change what happens next:

- **unavailable** — say so, show the remediation, and stop. There is nothing
  to review.
- **manual** — follow the instructions, and continue once the diff has come
  back through `kyrio ingest`.
- **error** — show the message as it came back. It is the tool's own, and it
  distinguishes a change that does not exist from a credential that expired.

Over roughly 40 files or 1,000 changed lines, say so and ask whether to review
the whole thing or a named part of it. A review that skims everything finds
nothing.

## 3. Read what it touches

The diff shows what moved, never what depended on it. Read the code around
each substantive change: the function it sits in, its callers, the tests over
it. Between three and eight files.

Note what you find, and keep it. You will compare it against the second
reading, not replace it with one.

## 4. Second opinion

Invoke `kyrio:review-pass`, passing the change identifier and nothing else.

It runs without this conversation and fetches the change itself. That is the
whole value: a review carried out with the context that produced the change is
theatre. Pass the identifier, never the diff — a diff passed as an argument is
a summary, and the thing worth finding is what a summary drops.

Wait for it. Do not review while it runs.

## 5. Reconcile

Merge the two sets of findings and mark each one:

- **both** — found twice, independently. Lead with these.
- **mine only** — I saw the surrounding code; say what that added.
- **theirs only** — I missed it, or I dismissed it. If you still disagree, say
  so and why, rather than dropping it silently.

Disagreement is information. A finding you argue against and explain is worth
more to the reader than one quietly removed.

## 6. Present, then ask

Show the reconciled findings, severest first, each with its evidence. Then ask
which to post. Do not assume all of them, and do not assume none.

## 7. Post only what was confirmed

Each comment needs a body file. Write it under the `output_root` from
`kyrio config explain` — never inside the repository being reviewed. A file
appearing in a repo whose maintainers never opted into any of this is a
conversation, not a commit.

If `output_root` is not set, say so and stop before drafting. It is set with
`kyrio probe record --output-root <path>`.

Draft first, always:

```sh
kyrio scm pr comment <id> --file <path> --line <n> -f <body>
```

That sends nothing. Show the draft. Then, and only on an explicit yes:

```sh
kyrio scm pr comment <id> --file <path> --line <n> -f <body> --post
```

One comment at a time. A batch confirmed in one breath is a batch nobody read.

Posting happens under the user's name. A wrong comment on a colleague's change
is a professional cost, and deleting it thirty seconds later does not unsend
the notification.

## Bounds

- Do not fix what you find. This is review, not repair.
- Do not approve, merge, or close anything.
- Do not comment on style or preference. Correctness, safety, tests, clarity.
- Say "no findings" when there are none. Inventing one to look useful wastes
  the author's time and costs you the next review.
