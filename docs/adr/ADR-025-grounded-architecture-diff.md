# ADR-025: A deterministic architecture diff with an optional, grounded AI explanation

- Status: accepted
- Date: 2026-10-03

## Context

A person reviewing an architecture change needs to know what changed and what that touches:
requirements, decisions, and what the engines find. A language model can explain a change in plain
words, but a model:

- restates facts loosely;
- invents effects ("faster", "more secure") and numbers;
- cites what it was never given;
- follows instructions planted in the text it reads;
- crowns a winner when asked to compare.

None of that may stand in for what changed, or for what an engine established.

What the repository audit found:

- **The IR's own diff:** elements matched by stable id, field-level changes, categories, and secret
  redaction (`core/architecture_ir/diff.py`). A same-architecture compare endpoint already existed,
  with capacity and cost as `null`.
- **Migration planning's change classification:** which engine reads which property.
- **Evolution's comparison of findings by stable id.**
- **Traceability links:** requirement references on elements, and ADR links through the ADR's
  related elements and the IR's decision references.
- **The architecture agent's model layer:** the `StructuredLlm` port, the schema checker, the output
  guard, and the knowledge retriever.
- **The web app** already calls a compare endpoint that does not exist.

## Decision

### Three layers, kept apart

1. **Deterministic diff.** The IR's diff, classified and grouped. The same two states always give
   the same changes, ids and groups.
2. **Deterministic impact.** Requirement relations come from traces and validation verdicts. ADRs
   are listed when an element they concern changed. The engines run on both states, and their
   findings are compared by stable id.
3. **AI interpretation.** Optional, separate, appended. It explains layers 1 and 2. It never
   restates, adds to or overrides them.

### Exact states, one answer when unavailable

A state is a named revision or an architecture agent run's candidate, never "the latest". Observed
states are drift's: compared there, not re-diffed here. A state that is missing, in another project
or organization, or not comparable, gets the same `compared_state_not_found` with its side. A hidden
state can't be told from a missing one.

### Groups by stated rules

Changes are grouped by deterministic rules, and every change is in exactly one group:

- connected changes: changed nodes and the endpoints of changed connections, never through an
  unchanged connection;
- traceability;
- descriptive;
- recorded reasoning;
- the architecture's own fields.

The model may title and explain a group by its id. It never forms or alters one.

### Impact only as far as it is established

- **Classes** say what a change concerns, never its effect.
- **Requirements:** a differing verdict is `potential`, never "violated". A scoped requirement
  without evidence is `undetermined`.
- **ADRs:** only proposed or accepted ADRs are listed, and they *may require review*.
- **Engines:** validation, reliability, security and observability run on both states with the same
  requirements and policy.
- **Capacity and cost** run only on a stored analysis the person names (its workload, its pricing
  snapshot). Otherwise they are `not_evaluated`, with why. A measure is reported only when the
  engine produced it for both states.
- **Nothing is scored.**

### The explanation is untrusted output

The model receives the diff, groups, impacts, engine results, unknowns, cited passages and the
person's context. Each is a delimited, untrusted data section, inside a versioned prompt
(`diff-explanation-v1`) with a closed schema.

Every statement cites what it rests on (a change, group, finding, requirement, ADR, passage or the
person's context), or is labelled an inference. The output is refused whole when it:

- cites what it was not given, or explains a group or requirement that isn't in the diff;
- states a number the data does not, or an outcome no cited engine finding establishes;
- uses score or winner language;
- asks a review question that does not ask;
- contains a URL, an address or a credential.

Only a timeout, a temporary provider failure or output off the schema is retried, once. Identical
states are `not_needed`, and the model is not called.

### Stored, append-only, minimal

A diff is stored immutably: both states by reference and content hash (never copies), its changes
(a secret's values never), its impacts and its engine results. Each explanation is an appended run
with:

- the prompt version, model and usage;
- the SHA-256 and size of the output;
- the validated explanation or why there is none;
- the citations it used.

A trigger refuses updates, deletes and truncation on both tables. There is no cache: comparisons
are synchronous and bounded (2,000 changes, a 60,000-character context, 10 passages, 2 model
calls).

### Nothing changes but the record

The diff writes no architecture, revision, agent run or analysis. It approves, migrates and deploys
nothing. Comparing and explaining need `architecture.analyze`; reading needs `architecture.read`.

## Consequences

- **Reviewable change.** Reviewers see what changed, why changes are grouped, and what the engines
  found in each state. Any explanation is visibly grounded or labelled an inference.
- **Fewer accepted explanations.** Pattern rules refuse whole outputs. A careful but plainly worded
  claim of an effect is refused unless it cites a finding. This is deliberate.
- **Pattern limits.** Numbers in words, unknown score phrasings and outcome wordings outside the list
  pass the checks. The prompt asks against them, but no guarantee is claimed.
- **Correctness is not judged.** AI Architecture Diff explains architectural changes; it does not
  determine architectural correctness by itself.
- **Different architectures are compared by element id alone.** A warning says so: a component kept
  under another id appears removed and added.
- **Off by default.** Without a configured model, explanations fail `llm_unavailable` and nothing is
  sent anywhere. The deterministic diff is unaffected.
