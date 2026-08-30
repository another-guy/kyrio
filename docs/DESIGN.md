# kyrio — design

A Claude Code plugin providing consistent software-engineering workflows on any
machine, regardless of which products that machine uses to host issues, source
control, CI, written knowledge, and observability.

**Status:** design agreed, implementation not started.
**Target runtime:** Claude Code only.
**Implementation language:** Python 3.12+, standard library only.

---

## 1. Purpose and non-goals

### Purpose

Nine kinds of daily engineering work — codebase comprehension, code review, bug
fixes, feature work, outage investigation, release preparation, end-to-end test
authoring, architectural design, and code archeology — are the same *judgment*
everywhere. What differs between machines is only how the diff is fetched, how
the comment is posted, and which binary answers the query.

This plugin splits on exactly that seam. Workflow knowledge is written once, in
provider-free prose. Access knowledge is isolated in a deterministic broker that
resolves each capability to whatever the local machine actually has.

### Non-goals

- **Not a replacement for vendor CLIs.** The broker wraps them; it does not
  reimplement them, and it exposes only the narrow slice the workflows need.
- **Not a compliance or data-protection tool.** Ensuring that data reaching an
  AI tool is permitted to reach it is a property of the environment, not of this
  plugin. See D1.
- **Not multi-environment.** One machine serves one environment. There is no
  selector, no profile switching, and no plurality anywhere in the model.
- **Not a desktop / claude.ai pack.** See D3.

---

## 2. Invariants

Numbered because they are referenced throughout, and because several are
mechanically enforced. An invariant that is only an aspiration erodes silently.

| # | Invariant | Enforcement |
|---|---|---|
| **I1** | **Provider-free skills.** No `SKILL.md` or shared reference names an access provider (issue tracker, source host, CI system, knowledge store, chat system, observability vendor, cloud). Languages, frameworks, and test tools are permitted. | `check_portability.py`, blocking |
| **I2** | **Environment-agnostic repository.** No file in the repo is derived from, or encodes the specifics of, any particular environment. All configuration lives outside the repo and is never committed. | `check_portability.py`, blocking |
| **I3** | **Self-sufficiency.** No component may depend on anything not shipped in this plugin, except features bundled with Claude Code itself. Personally-installed skills are not available on other machines and may not be required. | `check_portability.py`, blocking |
| **I4** | **One machine, one environment.** No configuration key, filename, CLI flag, or skill instruction may imply that more than one environment exists. | `check_portability.py`, blocking |
| **I5** | **No free-form execution.** `kyrio` never runs a command supplied by its caller. No `exec` verb, no passthrough flag, no shell interpolation of arguments. | code review + test |
| **I6** | **Draft-first.** Every write to an external system produces a draft by default. Sending requires an explicit `--post` flag, which a skill adds only after the user confirms. | code review + test |
| **I7** | **Non-invasive.** Nothing is written inside a git-tracked work repository unless the user explicitly asks. Generated artifacts default to the workspace knowledge area. | code review |
| **I8** | **Synthetic fixtures only.** No captured real payload enters the repository, redacted or otherwise. Every test fixture is hand-written. | `check_portability.py` scans `tests/fixtures/` |
| **I9** | **Determinism belongs to L1.** Anything that must be reproducible — query construction, time-window arithmetic, changelog generation, repo analysis — is implemented in the script layer, not described in prose for a model to perform. | code review |

### Why I2 matters beyond privacy

I2 is what makes the repository publishable in principle, and that property is
load-bearing for more than confidentiality. A repo that encodes no environment
is a repo that installs unchanged on a new machine, needs no fork, needs no
branch, and cannot drift between machines. The privacy property and the
portability property are the same property.

---

## 3. Architecture

Three layers. Each changes on a different schedule, which is the reason they are
separate.

```
L0   CONFIGURATION CASCADE      changes when tooling or conventions change
     machine -> workspace -> product -> repo
     JSON, discovered by walking up from cwd, merged per-key

L1   kyrio BROKER               changes when a provider is added
     deterministic Python 3.12+, stdlib only
     the ONLY place that knows any provider exists

L2   SKILLS                     changes when you learn a better way to work
     provider-free prose, one SKILL.md per workflow
     speaks only the L1 noun grammar
```

There is no fourth layer. The source design had a hooks and guardrails layer
whose entire purpose was mechanical compliance enforcement; with compliance out
of scope (D1), it guards nothing and is removed.

### The three seams

Removing the compliance layer would be expensive to reverse if the code had no
place to reattach it. Three seams are built now, at negligible cost, so that
reversal stays cheap (see D1):

- **S1 — single emit chokepoint.** Every response leaves `kyrio` through one
  `emit()` function. No adapter writes to stdout directly. This is also where
  output-shape versioning and tracing would attach. A unit test parses every
  module under `scripts/` and fails on a direct `print` or stream write.

  **The launcher shims are the one carve-out, and they have to be.** If no
  interpreter is found, Python never runs, so `emit()` cannot report it. Each
  shim therefore writes that single failure in the wire format by hand. The
  exception is bounded to one message per shim and is noted in both files.
- **S2 — declarative merge strategies.** The cascade resolver reads a per-key
  strategy schema rather than hardcoding one rule. A `monotonic-tighten`
  strategy is defined and left unused.
- **S3 — single inbound door.** `kyrio ingest` is the only path by which data
  originating outside the broker becomes broker-shaped.

### Repository layout

```
kyrio/                              private git repo; also its own marketplace
  .claude-plugin/
    marketplace.json
  plugins/kyrio/
    .claude-plugin/plugin.json
    bin/
      kyrio                         sh shim   -> exec interpreter __main__.py
      kyrio.cmd                     batch shim
    skills/
      setup/SKILL.md
      orient/SKILL.md
      trace/SKILL.md
      review/SKILL.md
      review-pass/SKILL.md          context: fork, user-invocable: false
      archeology/SKILL.md
      incident/SKILL.md
      ship/SKILL.md
      standup/SKILL.md
      bugfix/SKILL.md
      feature/SKILL.md
      e2e/SKILL.md
      design/SKILL.md
    references/                     templates and checklists shared by skills
    scripts/
      kyrio/
        __main__.py                 verb dispatch
        config.py                   cascade discovery + merge      (S2)
        emit.py                     response framing               (S1)
        ingest.py                   inbound normalization          (S3)
        probe.py                    interpreter, CLI, server detection
        repo.py                     local git and build-file analysis
        providers/                  the ONLY provider-aware code
      check_portability.py
    tests/
      fixtures/                     synthetic, hand-written        (I8)
  docs/
    DESIGN.md                       this document, once moved
  .githooks/pre-commit
```

`bin/` is added to the Bash tool's `PATH` while the plugin is enabled, so the
broker is invocable as the bare command `kyrio`. This matters for the permission
model (§7).

---

## 4. L0 — the configuration cascade

### Discovery

Starting from the working directory, walk upward. Every directory containing a
`.kyrio/config.json` contributes a layer. The machine layer is always the base.
Nearer layers win.

```
cwd = <workspace>/code/<product>/<repo>/src/api

  <repo>/.kyrio/config.json          build and test commands, selector policy
  <product>/.kyrio/config.json       service catalog, runbook wiring, templates
  <workspace>/.kyrio/config.json     shell flavor, artifact output root
  ~/.claude/kyrio/config.json        interpreter, capabilities, transports
```

Every layer is optional. A flat directory of unrelated clones declares nothing
and runs on the machine layer alone. A single large repository puts one layer at
its root. A workspace holding several products, each spanning several
repositories, puts the interesting layer at product level — which is also the
level at which a service catalog is actually true.

A layer may set `"root": true` to stop the upward walk at that directory.

### Why the product level exists

A service catalog — service to repository to dashboard to runbook to on-call
channel — is a fact about a *product*, not about a repository. Storing it per
repository duplicates it; storing it in the machine layer keyed by path makes
the machine layer a junk drawer. It also has a practical virtue: a directory
that groups repositories is not itself inside any of them, so a config file
there needs no discussion with the owners of any repository (I7).

### Merge strategies (S2)

The resolver reads a schema mapping each key path to a strategy. It does not
hardcode a single rule.

| Strategy | Behavior | Applied to |
|---|---|---|
| `nearest-wins` | The closest layer's value replaces all others. | scalars, transport selections |
| `deep-merge` | Maps merge key-by-key; nearer keys win. | `capabilities`, `conventions` |
| `union-append` | Lists accumulate across all layers, nearest last. | search roots, ignore patterns |
| `monotonic-tighten` | A nearer layer may only narrow, never widen. | *defined, currently unused — reserved for D1* |

### Shape

```json
{
  "schema": 1,
  "interpreter": "<absolute path to the probed interpreter>",
  "shell": "<shell flavor>",
  "output_root": "<workspace>/knowledge-base/kyrio",
  "capabilities": {
    "repo":  { "transport": "local" },
    "scm":   { "transport": "cli", "provider": "<provider-id>" },
    "issue": { "transport": "server", "tool_prefix": "<tool-namespace>" },
    "ci":    { "transport": "cli", "provider": "<provider-id>" },
    "kb":    { "transport": "unavailable" },
    "obs":   { "transport": "unavailable" }
  },
  "conventions": {
    "branch": "<pattern>",
    "test": "<command>",
    "build": "<command>"
  },
  "catalog": {
    "<service>": { "repo": "<path>", "dashboard": "<url>", "runbook": "<url>" }
  }
}
```

Machine-layer keys are written by `/kyrio:setup` from probing. Workspace,
product, and repo layers are hand-authored, and are derived first from whatever
the repository already states — ownership files, package manifests, project
files, CI definitions, an existing `CLAUDE.md` — before a new file is proposed.

Two consequences of these rules are worth knowing before hand-authoring a
layer. A layer declaring `"root": true` **contributes and then stops the walk**;
it is a boundary, not an exclusion, and the machine layer remains the base
regardless. And `deep-merge` gives a nearer layer no way to *delete* a key an
outer layer set: turning something off is an explicit value, such as
`{"transport": "unavailable"}`, never an absence. Silent removal is how a
cascade stops being predictable.

`kyrio config explain` prints every effective value alongside the layer that
supplied it, with each layer numbered so the number can stand in for a long
path on every row. This replaces the comments JSON cannot carry.

---

## 5. L1 — the `kyrio` broker

### Grammar

One local noun that needs no configuration and works on every machine
immediately, five transport-backed nouns, and three meta verbs.

```
LOCAL — no transport, no auth, no network, always available
  kyrio repo map                       entrypoints, module boundaries, build/test commands
  kyrio repo churn                     change frequency from history
  kyrio repo owners                    ownership from ownership files and blame
  kyrio repo blame <path>:<line>
  kyrio repo release-notes --since <ref>

REMOTE — transport resolved per machine
  kyrio issue get <id>
  kyrio issue list --mine [--current]
  kyrio issue comment <id> -f <file> [--post]
  kyrio scm pr diff <id>
  kyrio scm pr comment <id> --file <p> --line <n> -f <file> [--post]
  kyrio scm log --since <window>
  kyrio ci runs --branch <b> --since <window>
  kyrio kb search "<query>" [--since <window>]
  kyrio obs logs --service <s> --since <window> --level <l>
  kyrio obs metric --service <s> --name <n> --since <window>
  kyrio obs deploys --service <s> --since <window>
  kyrio obs alerts

META
  kyrio caps                           what this machine can reach, and gaps
  kyrio config explain                 effective config, with source layer
  kyrio ingest <kind> --file <path>    the single inbound door (S3)
```

`kb` unifies searching written knowledge and searching discussion history. Both
answer the same question — *what did people write about this* — and both return
the same shape. Consumers fan out across every configured provider, including a
local knowledge directory, which requires no transport at all.

There is no `cloud` noun. It appeared in the source design's capability table
and was called by none of its twelve workflows.

### Response protocol

One line of JSON, a `---` delimiter, then the payload verbatim.

```
$ kyrio scm pr diff 4821
{"status":"ok","kind":"diff","source":{"transport":"cli"},"files":12,"insertions":83}
---
diff --git a/src/checkout/handler.ts b/src/checkout/handler.ts
@@ -88,7 +88,11 @@
-    const r = await gateway.post(req);
+    const r = await gateway.post(req, signal);
```

Payloads are printed raw rather than escaped into a JSON string field. Most of
what the broker returns is multi-line text — diffs, log lines, commit bodies,
ticket descriptions — and embedding those in JSON makes them both more expensive
and harder to read, for no gain: the header alone carries everything a program
needs to parse, and one `readline` plus `json.loads` recovers it.

**A response with no payload is a header line and nothing else — no delimiter.**
So a reader's rule is: take line one as the header; if line two is `---`,
everything after it is the payload. `call` and `unavailable` are complete in
the header and carry no payload; passing one raises rather than emitting, since
it can only mean a programming error. `manual` carries its instructions as the
payload, and `error` may carry detail.

### Statuses

| `status` | Exit | Meaning |
|---|---|---|
| `ok` | 0 | Header plus payload. Proceed. |
| `call` | 0 | This is not the result. Call the named tool, then continue. |
| `manual` | 0 | No automated transport. Instructions for the user; wait. |
| `unavailable` | 0 | Not configured on this machine, with the remediation. |
| `error` | 1 | Bad arguments, broken transport, expired auth. |

A non-zero exit means the program failed. Delegation and manual transport are
ordinary control flow, not failures, and reporting them as failures invites
retrying, apologising, and hunting for alternative commands.

### Delegating to a tool the broker cannot call

Python cannot invoke a Claude Code tool. Where a capability is served by a
connected server rather than a binary, the broker returns the call to make:

```
$ kyrio issue get PROJ-1234
{"status":"call",
 "tool":"<tool-namespace>__<tool-name>",
 "args":{"id":"PROJ-1234"},
 "expect":["title","state","assignee","description","acceptance criteria","links"],
 "next":"This is not the ticket. Call the tool named above, then continue."}
```

The result is then read directly. It is **not** round-tripped back through the
broker for normalization. Normalization earns its cost when a deterministic
consumer reads the output — release-note generation, churn arithmetic — and
those operations are local by nature and never take this path. When the consumer
is a model, normalization buys little and costs a great deal: the payload would
have to be re-emitted through the model's own output to get back into the
broker, doubling tool calls, tripling the payload in context, and introducing a
failure mode with no recovery, where a paraphrased re-emission produces a
confident and subtly wrong result.

The `next` field makes each response self-describing, so the protocol needs no
standing instruction injected into every session and no skill has to restate it.

### Transport resolution

Per capability, in order: a connected server, then a CLI, then a browser-driven
path, then manual. A capability with no working transport reports `unavailable`
with its remediation — **not** `manual`. Manual means the user is the transport;
a pack that silently degrades into asking the user to paste things is worse than
one that says a capability is not configured here and how to fix it. Manual
remains available as a deliberate per-capability opt-in.

A capability may declare an ordered provider list rather than a single provider,
which is how a machine mid-migration between observability products is
expressed: try the first, fall through to the next. No skill learns that a
migration is underway.

### Adapter contract

Every adapter under `providers/`:

- constructs arguments; never interpolates caller input into a shell string (I5)
- runs its binary via `subprocess` with an argument list, never `shell=True`
- parses to the broker's shape and returns it; never prints (S1)
- has unit tests for argument construction and output parsing, against
  hand-written fixtures (I8)

`providers/` is the only path where provider names may appear (I1).

---

## 6. L2 — skills

### Catalog

Twelve workflows. Every "uses" line below is provider-free; that is the
acceptance criterion (I1).

| Skill | Work it covers | Uses |
|---|---|---|
| `/kyrio:setup` | Re-runnable install: probe, report, propose. | `probe`, `caps` |
| `/kyrio:orient` | Breadth-first comprehension. Map, boundaries, build and test commands, hotspots, ownership. | `repo map`, `repo churn`, `repo owners` |
| `/kyrio:trace` | Depth-first comprehension. One action through every layer, as an ordered file list and a sequence diagram. | `repo map` |
| `/kyrio:review` | Review an existing change, with fresh eyes. | `scm pr diff`, `scm pr comment` |
| `/kyrio:review-pass` | The fresh-context judgment step. Not user-invocable. | `scm pr diff` |
| `/kyrio:archeology` | Why does this exist, and what breaks if it goes. Blame, to the change, to the ticket, to the discussion, as one chronology. | `repo blame`, `scm`, `issue`, `kb` |
| `/kyrio:incident` | Timeline before hypothesis. Then a hypothesis ledger where each candidate gets the query that would falsify it. | `obs deploys`, `obs logs`, `obs metric`, `scm log` |
| `/kyrio:ship` | Release preparation. Changelog generated deterministically, risk scored against churn, rollback drafted. | `repo release-notes`, `issue`, `ci`, `obs deploys` |
| `/kyrio:standup` | Assigned work, review queue, red pipelines, open alerts. One screen. | `issue list`, `scm`, `ci`, `obs alerts` |
| `/kyrio:bugfix` | No fix is written until a failing test reproduces the bug. | `issue`, `scm` |
| `/kyrio:feature` | Implement in reviewable slices; self-review before handing over. | `issue`, `ci` |
| `/kyrio:e2e` | Browser test authoring and flake triage against a pinned selector policy. | `ci`, repo-layer conventions |
| `/kyrio:design` | Decision record against a template: problem, constraints, options with honest tradeoffs, recommendation, and what the decision forecloses. | `kb`, `repo map` |

Several of these overlap with skills bundled with Claude Code or installed
personally. Overlap is **not** a reason to omit them (I3): a personally
installed skill exists on one machine, and a pack that silently depends on it is
not portable. Where a bundled Claude Code feature covers the same ground, the
kyrio version still earns its place by being broker-aware — able to reach a
change hosted anywhere, and to write back under the draft-first policy (I6).

### Authoring rules

- **Never name an access provider.** If a skill cannot move to a machine with
  entirely different tooling without editing, provider knowledge leaked into the
  wrong layer (I1).
- **Stay under ~5,000 tokens.** After auto-compaction, Claude Code re-attaches
  only the first 5,000 tokens of each skill, within a 25,000-token budget shared
  across all invoked skills. Longer material belongs in `references/`, read on
  demand.
- **Skill content persists across turns**, so every line is a recurring cost.
  State what to do; do not narrate why.
- **Describe judgment, not access.** Sequence, gates, quality bars, when to stop
  and ask. Anything that must be reproducible belongs in L1 (I9).
- **State the boundary in the description**, not just the trigger — "for
  reviewing an existing change; not for reviewing uncommitted local work" —
  because twelve skills in one namespace is enough for automatic invocation to
  misfire.
- **`disable-model-invocation: true`** on `setup` and `ship`, which should run
  only when asked.

### The fresh-eyes pattern

Work that grades itself passes. Reviewing a change with the same context that
produced it is theatre, so the judgment step must not see the conversation.

`context: fork` gives exactly that — the skill body becomes the subagent's
prompt, with no access to conversation history. But a forked skill cannot
confirm anything with the user, since the user is not there. So workflows that
need fresh eyes split in two:

```
/kyrio:review 4821                       main thread
  kyrio scm pr diff 4821                 fetch, summarize
  invoke kyrio:review-pass  "4821"       context: fork
                                         background: false
                                         user-invocable: false
      no conversation history
      re-fetches the change itself
      returns findings only
  present findings, confirm, then --post  main thread
```

The inner skill receives an identifier, never a payload — passing a large diff
as an argument reintroduces exactly the re-emission cost the response protocol
avoids.

`background: false` is required: a backgrounded fork runs with a narrower tool
set that would exclude the broker. Note also that a forked skill's edits fall
outside session checkpoints.

No subagent files ship. See D2.

---

## 7. Installation, setup, and permissions

### Distribution

A single private git repository that is simultaneously the plugin and its own
marketplace, so installation takes a git URL and nothing else needs hosting.

```
/plugin marketplace add <repo-url>
/plugin install kyrio@kyrio
/kyrio:setup
```

For a machine that gets rebuilt, `extraKnownMarketplaces` and `enabledPlugins`
in `~/.claude/settings.json` make the install survive.

The repository is private, so each machine needs credentials to reach it.
`/kyrio:setup` verifies reachability of the marketplace remote and reports
plainly if it cannot, so that a failed update months later has a diagnosis
rather than a symptom.

### What setup does

Probing is **by execution, never by presence**. A name resolving on `PATH`
proves nothing — on some systems a placeholder shim resolves and does nothing
useful. Every probe runs the binary and checks that it answers, and separately
checks authentication state, because *installed* and *logged in* are different
failures with different fixes.

Connected-server discovery prefers `claude mcp list`, because only that
distinguishes connected from needs-auth from failed, and that distinction is the
entire reason to re-run setup. Parsing configuration files is the fallback.
Results cache under `~/.claude/kyrio/state/`.

```
$ /kyrio:setup

INTERPRETER   python 3.12.x   <absolute path>            ok

CAPABILITY    TRANSPORT       STATUS
  repo        local           ready
  scm         cli             ready
  issue       server          connected
  ci          cli             NOT AUTHENTICATED
                                -> run: <exact command>
  kb          --              unavailable
                                -> no provider configured
  obs         --              unavailable

WRITES
  ~/.claude/kyrio/config.json                      automatic
  ~/.claude/kyrio/state/interpreter                automatic
  ~/.claude/settings.json  permission rule         on confirmation

INSTALLS      nothing, ever.
```

The interpreter is recorded twice on purpose. `config.json` holds it as a
normal key; `state/interpreter` holds the same absolute path as one line of
plain text, because the launcher shims are `sh` and batch and cannot parse JSON
without an external tool — and requiring one to start the broker would defeat
the point. Setup writes both or neither.

The shims resolve an interpreter by execution in this order: `KYRIO_PYTHON`,
the recorded path, then the names on `PATH`. That order exists because the
bootstrap case is a machine with no configuration at all: the shim has to work
before setup has ever run, and prefer setup's answer once it has.

Setup **never installs software and never runs an authentication flow.** It
prints the exact command and stops. Installing software on a managed machine is
a decision with an owner, and a script cannot know what is permitted.

Setup is idempotent. Re-running it after a server is connected upgrades that
capability's transport and leaves everything else untouched. That is the whole
re-runnability story.

### The permission model

Two mechanisms, because one is not enough:

```yaml
# in each SKILL.md — covers the turn that invokes the skill
allowed-tools: Bash(kyrio:*)
```

`allowed-tools` grants permission only for the invoking turn; the grant clears
on the user's next message. Every workflow here is multi-turn, so setup also
proposes a session-wide rule, shows the exact JSON, and writes it only on
confirmation:

```json
{ "permissions": { "allow": ["Bash(kyrio:*)"] } }
```

The rule names the bare command rather than a path, because `bin/` is on the
Bash tool's `PATH` while the plugin is enabled, and a path-based rule would name
a version-specific directory and break silently on the next plugin update.

**This is one of the broker's strongest justifications.** Without it, a workflow
calling four different provider CLIs needs four allow rules and prompts on
anything unanticipated. With it there is exactly one rule, forever, and adding a
provider never touches permission settings again.

That property depends entirely on I5. The moment `kyrio` accepts a
caller-supplied command to execute, a single narrow allow rule becomes a blanket
shell grant.

---

## 8. Side-effect policy

### Writes to external systems

Comments post under the user's identity. A wrong comment on a colleague's change
is a professional cost, not a technical one, and deleting it thirty seconds
later does not unsend the notification.

So write verbs draft by default and require `--post` to send (I6). A skill adds
that flag only after the user has seen the content and confirmed:

```
kyrio issue comment <id> -f note.md
   -> {"status":"draft"} plus the rendered comment; nothing sent

kyrio issue comment <id> -f note.md --post
   -> sent
```

### Writes to the filesystem

Generated artifacts default to a knowledge area at the workspace level, outside
every git-tracked repository:

```
<output_root>/
  maps/<product>/<repo>.md
  reviews/<change-id>.md
  incidents/<date>-<service>.md
  archeology/<symbol>.md
  releases/<tag>.md
```

Nothing is written inside a work repository unless explicitly requested (I7).
Two reasons: a dirty working tree at the moment the user is about to inspect
their own diff is genuinely disruptive; and a generated file appearing in a
repository whose maintainers never opted into any of this is a conversation, not
a commit.

`output_root` is a cascade key, so a workspace or product layer can redirect it.

---

## 9. Testing and lint

### `check_portability.py`

One script, two rules, run as a `pre-commit` git hook and in CI, blocking in
both.

```
RULE 1  repo-wide
  environment-coupling vocabulary
  hostname-shaped strings
  identifier patterns that look like real keys
  applies to tests/fixtures/ as well          (I2, I4, I8)

RULE 2  skills/** and references/**
  access-provider names
  references to skills not shipped here and not bundled   (I1, I3)
  permitted: languages, frameworks, test tools, file patterns, git

ALLOWLIST
  scripts/kyrio/providers/**   the only provider-aware path
```

The check is framed, correctly, as a portability check rather than a redaction
check: a plugin intended to run in any environment must not couple to a
particular one. That is an ordinary engineering concern, and the framing keeps
the check's own configuration from being the one file that reveals what the
check exists to prevent.

### Tests

| Surface | Approach | When written |
|---|---|---|
| Cascade discovery and merge strategies | `unittest`, synthetic layer trees | proactively — this is real logic with real edge cases |
| Response framing (S1) | `unittest` | proactively |
| Adapter argument construction and parsing | `unittest` against hand-written fixtures (I8) | with each adapter |
| Skill triggering and behavior | `claude plugin eval` suites | **reactively**, one per observed misfire |

The asymmetry is deliberate. An eval written before a skill has misfired tests
the author's imagination rather than the skill. Confirm `claude plugin eval`
availability on the account before depending on it.

**Fixtures are hand-written and synthetic, always (I8).** The convenient way to
build an adapter fixture is to capture a real response and save it — and a real
response carries identifiers, usernames, hostnames, and internal URLs. Rule 1 of
the lint scans the fixture directory precisely because this is the most likely
place for the discipline to slip.

---

## 10. Build order

Each phase produces something usable. Stop at any point where the next phase is
not pulling its weight.

| Phase | Deliverable | Why here |
|---|---|---|
| **P0** | Broker skeleton: cascade resolver, `emit` (S1), `ingest` stub (S3), `repo` capability, minimal `setup` (interpreter probe, permission rule), shims, lint, CI | Nothing else works until this is real |
| **P1** | `/kyrio:orient`, `/kyrio:trace` | **Zero transports, zero configuration.** Useful on any machine the moment the plugin installs, and they validate the skill-authoring discipline before any provider work |
| **P2** | Full `setup` probing, `scm` capability, `/kyrio:review` + `/kyrio:review-pass` | Tracer bullet. One transport, highest-frequency workflow, exercises the entire loop: broker fetch, fresh-context judgment, draft, confirm, post |
| **P3** | **Port to a second machine** | Before writing skill five. Porting with three skills surfaces leaks cheaply; porting with nine means rewriting nine |
| **P4** | `issue` and `kb` capabilities, `/kyrio:archeology` | The workflow nothing else can do, and the one that proves the multi-capability model |
| **P5** | `obs` capability, `/kyrio:incident` | Highest stakes, and where deterministic query construction (I9) pays for itself immediately |
| **P6** | `ci` capability, `repo release-notes`, `/kyrio:ship` | Deterministic generation from history; a paraphrased changelog eventually drops the line that mattered |
| **P7** | `standup`, `bugfix`, `feature`, `e2e`, `design` | On demand only |

**P3 is not optional and not reorderable.** It is the only step that tests I1
against reality rather than against intent.

### When to write skill number *n*

A workflow is worth a skill once the same paragraph of context has been typed
three times. Written earlier, it encodes a workflow not yet settled — and a
wrong skill costs more than no skill, because it fires when unwanted and erodes
trust in the whole pack.

---

## 11. Deferred decisions

Each records the trigger that reopens it, so the decision is made with evidence
rather than re-litigated from memory.

### D1 — Data-protection layer

**Deferred.** Ensuring that data reaching an AI tool is permitted to reach it is
a property of the environment — an agreement with the provider, synthetic data
only, or no regulated data present — and is the user's responsibility, not the
tool's. No redaction, no classification config, no blocking hooks.

**Trigger:** the tool is asked to handle data whose exposure is constrained.

**Retrofit path,** approximately one day given the seams:

| Component | Attaches at |
|---|---|
| Redaction applied to all output | S1, one call site |
| Compliance block in configuration | S2, one schema entry using `monotonic-tighten` |
| Externally-sourced data normalization | S3 |
| Blocking guards | new `hooks/hooks.json`, purely additive |

**The one thing not cheap to recover:** a capability served only by delegation
has no direct adapter, and redaction cannot apply to a payload the broker never
touched. Closing that hole means writing the adapter that was skipped. That cost
is proportional to how heavily delegation is relied upon.

### D2 — Parallel exploration workers

**Deferred, and flagged as the likely next optimization.**

`context: fork` covers every case where fresh context is needed, because those
have one fixed prompt and run one at a time. A shipped subagent buys exactly one
additional capability: **many workers in parallel, each with a different prompt
written at call time.**

That is what deep tracing wants. Following one action through a large codebase
decomposes at runtime into several independent explorations — the handler, the
route it reaches, the service behind it — each a different question. Serially
this is several round trips; concurrently it is one.

**Trigger:** `/kyrio:trace` (P1) is measurably slow on a real codebase.

**Shape when added:** one `agents/explorer.md` — read-only, fresh context —
dispatched explicitly by `trace` and `archeology` at named steps, never left to
autonomous discretion.

### D3 — Desktop and cloud runtime

**Deferred.** That runtime executes in a container with no access to the working
tree, the provider CLIs, or the network the broker needs, and skill bodies there
cannot run shell commands. The broker is unreachable by construction, so those
skills would be a different species — pure synthesis over pasted material — not
thinner copies. Frontmatter there is also restricted to six fields.

**Trigger:** a genuine need to run synthesis work away from the machine.

**Shape when added:** new skill files sharing `references/` templates with their
Claude Code siblings, so output shape stays identical regardless of where the
work ran. Not a conversion of existing skills. Runtime targeting declared in
`metadata`, which is machine-readable and survives upload, rather than as a tag
in `description`, which is load-bearing for automatic invocation.

### D4 — Alternative configuration format

**Deferred.** Configuration is JSON: both a reader and a writer exist in the
standard library, and hand-authored layers are small. `tomllib` is available at
the chosen Python floor but is read-only, and the machine layer must be written
programmatically.

**Trigger:** hand-authoring product-layer configuration becomes painful enough
that comments are missed.

**Shape when added:** accept TOML for hand-authored layers only; continue
writing JSON. `kyrio config explain` already covers most of what comments would.

---

## 12. Rejected alternatives

Recorded so they are not re-proposed.

| Alternative | Why rejected |
|---|---|
| **One skill per workflow per environment** | The judgment in reviewing a change is identical regardless of what hosts it. Splitting on that axis multiplies files while duplicating the part that never varies. Split on access instead. |
| **YAML configuration** | No YAML reader in the standard library. Requiring a package installation is precisely the operation a restricted network blocks. JSON is native to both reader and writer. |
| **A compiled single-file binary** | Would remove the runtime dependency, but plain-text source is an asset here: a repository that can be read end to end supports I2 in a way a committed binary cannot. Unsigned executables are also quarantined on managed machines. |
| **Node as the implementation runtime** | Not guaranteed by the harness, which ships as a self-contained binary. Version-manager installs are commonly shell-session-scoped, so a script invoked outside that shell may not find the runtime at all. |
| **Non-zero exit codes for delegation and manual transport** | A non-zero exit reads as failure and invites retrying and hunting for alternatives. Neither case is a failure. Reserve non-zero for genuine errors. |
| **Round-tripping delegated results through `ingest`** | Buys byte-identical response shapes, paid for with a payload crossing the model boundary three times, two tool calls instead of one, a normalizer per provider, and silent corruption whenever a re-emission paraphrases. Normalization is valuable for deterministic consumers, and those never take this path. |
| **The broker speaking the tool protocol directly** | Conceptually the cleanest — no delegation at all — but remote servers authenticate through flows whose credentials live in the harness's own storage. Reaching into them is fragile and out of bounds. Viable only for local servers, which is not enough to build on. |
| **A `cloud` capability** | Present in the source design's capability table; called by none of its workflows. Add it when a workflow wants it. |
| **Separate `doc` and `chat` capabilities** | Same consumer, same result shape, and the two hardest transports to build. Merged into `kb`, which is also the query archeology actually wants. |
| **A guardrails layer** | Existed solely to make compliance mechanical. With D1 deferred it guards nothing, while a plugin-shipped hook fires in every session on the machine, including those that never touch this plugin. |
| **Shipping subagent definitions now** | `context: fork` already provides fresh context. A subagent adds only parallel workers with per-call prompts, which nothing in P0–P3 needs. See D2. |
| **Mirroring the repository into separately-administered internal hosting** | Best network and policy fit, but places personal tooling into version control administered by someone else, and multiplies the number of places a release must be pushed. |
| **A public repository** | Would remove the credential requirement on each machine entirely, and I2 makes it safe in principle. Rejected because a mistake in a public repository is archived by third parties within minutes, whereas a private one can be corrected. |
| **Omitting workflows that overlap bundled or personal skills** | A personally installed skill exists on one machine. Depending on one violates I3 and silently breaks portability. |
| **Automatic fallback to manual transport** | Turns every unconfigured capability into a prompt asking the user to fetch things by hand. Reporting `unavailable` with a remediation is more useful and more honest. |
| **Setup installing missing software** | A script cannot know what a managed machine permits, may need elevation, may install a version the team does not use, and leaves the machine half-configured when it fails midway. |
