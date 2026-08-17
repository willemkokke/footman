# The typed call fabric — the shape behind the transports, generalised

Status: THOUGHT EXPERIMENT — 2026-08-17. **Not about footman as it is.**
Nothing here is a plan, nothing is ruled, and nothing below commits footman to
becoming any of it. Opened by Willem to see what the transports frame is once
it is deliberately pulled *away* from footman: what is the general system, who
has built pieces of it before, and where the hard problems actually sit.

Neighbours, and this note leans on both rather than restating them:

- [20260816-services-and-sinks.md](20260816-services-and-sinks.md) — Part 3 is
  the concrete transports frame this note generalises: one signature, projected
  into argv, documents, sockets, completion, help, schema.
- [20260817-global-address-space.md](20260817-global-address-space.md) — the
  data half: the once-cell key as a name, the cache-key/address distinction,
  the three result tiers this note extends to four.

## The prompt (Willem)

> I've been thinking about using type signatures as a method of rpc, with
> various pluggable transports. cli to function call is one transport,
> autocomplete another, different process over ipc, different process over
> socket. […] Let's try and generalise this concept away from footman as it
> is, and see where it could take us.

and, once the generalisation was on the table, the follow-up that produced
most of this note:

> The thing I'm least clear on is the security aspect.

## The generalised claim

Strip the footman specifics and the thesis is: **a typed function signature is
a complete interface contract, and everything else — CLI grammar, completion,
help text, docs, JSON schema, wire formats, cache keys, data addresses — is a
derived view of it.** One law (views must not drift) and one failure mode (two
views disagreeing about one signature). The M1/M2 bugs from #454 were view
drift; the proposed same-call-every-transport property test is the law written
down.

Three inversions distinguish this from RPC as the field knows it (CORBA,
gRPC, Thrift — contract-first, generated stubs, machine-only):

1. **The native type system is the IDL.** No separate contract language, no
   codegen step to drift.
2. **Humans are peer endpoints.** argv, TAB, and help text are transports, not
   decoration. No RPC system has treated the person at the keyboard as a
   first-class caller of the same contract.
3. **Transports split into two kinds.** *Executing* transports carry a call to
   a body: in-process, argv, document, socket, remote. *Reflective* transports
   carry a call **without executing it**: completion (a partial call → its
   legal continuations), help, `--describe`, docs export, a dry-run plan.
   Autocomplete is not a ninth odd projection; it is the canonical member of
   the reflective family, and the manifest is that family's store — which is
   *why* it can answer in 30 ms: reflection was designed never to touch the
   executing path. (Dynamic `suggest()` completers look like a crack in this
   split — they execute code at TAB time. They are not; see the refinement
   below.)

### Reflection that executes: where `suggest()` fits

The refined statement: **the reflective family never executes *the task*; it
may execute *queries about the world* — and those queries are themselves
fabric calls.**

Completion answers two different kinds of question, and only one is
answerable from the contract alone. *"What does the signature admit?"* —
flags, subcommands, `Literal` choices — is static, derivable, baked into the
manifest: reflection proper, zero execution. *"What does the world currently
contain?"* — which files, which git branches, which deployed environments —
cannot be enumerated by any contract; only a query can answer it, and
`git checkout <TAB>` is impossible without asking the repo.

A `suggest()` completer is that query: a function from a partial binding to a
set of legal continuations — a typed callable with a contract of its own. So
completion-with-suggest is the fabric calling itself: the reflective layer is
a *client* of the executing layer, for a restricted class of calls. footman's
architecture already treats it exactly this way without saying so — the hot
path answers from the manifest import-free, and `_suggest.py` spawns a
separate child to run one completer fresh. Not a violation; a second, smaller
RPC riding beside the first.

The types-flavoured reading: `Literal["staging", "prod"]` is a static domain;
`suggest()` is the escape hatch for a **dependent domain** — legal values
that depend on the world's state or on arguments already bound. Static
domains project into the manifest; dependent domains need an oracle, and the
oracle is a call.

Once suggest-is-a-call is admitted, the rest of this note applies to it
mechanically — the test of whether the frame is any good:

- **Effects:** a completer must be a derivation-shaped query — read-only,
  fast, no actions. TAB must never deploy anything. In the effect vocabulary
  that is enforceable at declaration, not aspirational.
- **Security:** the sharp edge. If TAB executes code, mounting an untrusted
  tree means *TAB runs their code* — the reflective attack surface includes
  execution, not just steering-by-description. Every shell's completion
  system has quietly had this problem forever (bash completion functions are
  arbitrary shell code). The fabric's answer is the attenuation story: a
  completer runs as a sandboxed derivation with read-only authority over its
  declared input set; an untrusted manifest's completers run confined or not
  at all, and TAB degrades to static-only for that subtree.
- **Addressing:** a suggest call has a name too —
  `(completer, partial binding, world fingerprint)` — so the caching ladder
  applies: bake what is static, cache what is stable, run fresh what is live,
  stale-while-revalidate in between. footman's existing spectrum (baked
  choices at one end, `_suggest.py` re-running fresh at the other) is this
  ladder with the middle rungs not yet built.

## The answer to Waldo

The classic objection is Waldo et al., *A Note on Distributed Computing*
(1994): systems that pretend remote calls are local calls die on latency,
partial failure, and concurrency. CORBA and DCOM died on exactly that.

The transports table in the services note is an answer to Waldo:
**transport-aware uniformity instead of transport transparency.** One
contract, many transports, with the differences *typed rather than hidden* —
what shapes can cross (live object > serialisable > strings), where the trust
boundary sits, which markers are properties of the value (`between`) versus
properties of a world (`exists`), and a failure vocabulary that distinguishes
"could not reach it" from "it ran and failed". Per-marker transport-legality
generalises to a lightweight effect discipline: every contract predicate is
classified by *which world evaluates it*. That is the piece CORBA never had.

## A call is a name

The global-address-space note gets within a sentence of the general theory.
The sentence: **calling is resolving a name, and execution is the cache-miss
handler.**

`(canonical address, arguments in normal form, input fingerprint)` is a name.
Resolution walks a lattice of stores — the run's once-cell, a project rung
(daemon registry, persisted cache), a fleet's content-addressed store — and
only on a miss does anything run. Under that frame, separately-designed things
collapse into one resolver with four *result kinds*:

| kind | the name resolves to | needs |
|---|---|---|
| **skip** | permission not to run | a key; no serialiser |
| **value** | the typed return | serialisation it already has |
| **artifact** | bytes in a store | declared outputs, a store |
| **endpoint** | a live capability — "an instance of pyright for config X" is a name whose resolution is a socket, not bytes | a lease |

The fourth kind is what the services design was reaching for: the daemon
registry, the once-cell, and an artifact store are one namespace resolved
against stores of different lifetimes. It also makes the job/service
distinction compositional: a job's name resolves to a value; a service's name
resolves to a capability with a lease.

## Prior art — nobody has the whole shape

| system | has | lacks |
|---|---|---|
| Unison | content-addressed *code*: a function's true name is its hash, distribution is "ship the hash" | human transports; results addressing is secondary |
| Nix / Bazel | the data address (derivation = the name triple) | live calls, services, human transport |
| Cap'n Proto / E-lang | the trust answer: capabilities authorize invocation, promise pipelining | the reflective family; adoption |
| Erlang/OTP, Ray | placement independence | a typed contract; more than one runtime |
| COM typelibs, PowerShell | one signature driving machine *and* human views (typelib → vtable + VBA; cmdlet → pipeline + completion + help) | one machine, one ecosystem |
| OpenAPI | contract-to-many-views | a contract any compiler checks |

That each has exactly one piece is itself informative: the pieces compose, and
no one has stood where all of them are cheap. The graveyard's common cause is
also worth naming: the systems that died (CORBA, DCOM, SOAP) were
contract-first with codegen, all-or-nothing distributed object models, and
transparency liars. Derived views instead of generated stubs, rungs that each
pay for themselves locally, and typed transport differences are the three
structural reasons this shape is not a rerun.

## The transport the list missed: agents

An LLM agent is just another caller, and the reflective family is how it
discovers what it may call. MCP tool definitions are typed signatures
published with JSON schema and a description — a manifest is already ~90% of
an MCP server description. **Completion for a human and tool discovery for an
agent are the same reflective transport with different renderings.** Any
system built on this shape gets agent-callable nearly for free, with the trust
ladder already in place to say what an agent may reach. In "where could this
go", this rung outranks the fleet rung: it needs no new machinery, only a
renderer — and the security section below is most urgent here.

## Leaving Python

Cross-language, an annotation cannot be the IDL — but the successor already
exists: **the manifest becomes the actual contract, and language-native
signatures become frontends for authoring it.** Signatures-in-your-language →
neutral serialised contract → projections. That inversion is what would let a
Rust binary, a TypeScript service, and a Python tasks file mount into one
address space. It also sharpens the provider-canonical ruling from the
address-space note: identity must live in the neutral layer, because no one
language's namespace is global.

The other export is a minimal effect vocabulary, made by crossing two axes the
existing notes already found — completion condition (job/service) ×
addressability (pure/impure):

- **derivation** — pure job: addressable, skippable, safe to offload and replay
- **action** — impure job: runs every time (`shared=False` is its footman
  spelling); the only kind needing real authorization
- **resource** — service: a leased endpoint with readiness and teardown

Three kinds, not a research effect system, and all three already have footman
spellings — decent evidence the vocabulary is real rather than invented.

### What a second language actually needs

> I love python, but if other languages can't be bridged, it's not much of an
> ecosystem. — Willem, 2026-08-17

Two roles, and they are separable. A **provider** authors callables, emits
manifest entries, and answers the call envelope. A **consumer** mounts a
manifest and issues calls. The floor for a provider is deliberately low: read
a JSON document on stdin, write one on stdout — the document transport is the
lingua franca, and everything above it is per-language ergonomics:

| language | authoring frontend | why that is the natural fit |
|---|---|---|
| Python | runtime annotations, introspected | annotations survive to runtime; footman is the existing proof |
| TypeScript | value-level schemas that infer static types (the zod pattern) | TS types are erased at runtime; the ecosystem already rebuilt them as values, and tRPC proves signatures-as-RPC works there |
| Rust | proc-macro on the fn; manifest baked at compile time, emitted via `mybin --manifest`; serde is the binder | clap derive already does signature→CLI; the manifest becomes a build artifact |
| Go | struct tags + reflection, or `go:generate` | the idiom Go already uses for every serialisation boundary |
| anything, incl. shell | a handwritten sidecar manifest + envelope conformance | participation must not require a smart runtime |

Four structural points keep this from being CORBA's language-mapping hell:

1. **The binder enforces; stubs are only ergonomics.** Contract enforcement
   happens at the boundary, in the envelope binder, not in the consumer's
   type system. A consumer language that cannot express a tagged union
   renders it lossily and keeps runtime safety anyway — weak consumers lose
   compile-time help, never correctness. CORBA's mappings were load-bearing
   and therefore had to be perfect; these are lossy-but-safe.
2. **In-process stays language-private.** Live objects never cross a language
   boundary; cross-language is *always* at least the document transport. The
   transport-legality ladder already says this — "bridging" never means FFI
   or a shared runtime.
3. **The reflective layer is already language-neutral**, and footman
   accidentally proved it: `_complete.py` answers TAB from a file read and a
   JSON parse without importing user code, so it literally cannot care what
   language produced the manifest. A Rust-provided tree completes identically
   to a Python one, today, by construction.
4. **Coherence across N languages is a conformance kit, not trust.** The
   same-call-every-transport property test, packaged: a golden manifest plus
   golden call/response pairs every frontend must pass. LSP is the
   sociological precedent — one JSON protocol plus a spec produced faithful
   implementations in every language without a central codegen authority.

The neutral contract itself is an intersection type system with an extension
vocabulary: scalars, containers, records, tagged unions, optionals — plus the
markers/caveats, effect kinds, and world-denoting types. Protobuf proves the
intersection approach scales; Amazon's Smithy is the closest existing neutral
layer (its "traits" are exactly the marker concept); the WASM component
model's WIT is the same idea with a sandbox attached, and is a plausible
*optional* execution substrate — one rung among the transports, not a
requirement.

## Three layers, one law — the encoding is an implementation detail

> json as protocol is really an implementation detail right? Transports could
> define their own as long as it can represent the schema? — Willem, 2026-08-17

Right, and it separates three things "protocol" says loosely:

1. **The contract** — the type layer: what shapes exist, what markers mean,
   what a refusal is. Singular, canonical, language- and wire-neutral.
2. **The envelope** — the call semantics: bind these arguments, run, return a
   typed result or a typed refusal, attach the receipt. Also singular; this
   is what the 0.21.0 process-boundary work actually defined, and what the
   coherence law protects.
3. **The encoding** — how envelope + values become bytes on one transport.
   **Plural, negotiable, per-transport.** JSON is just one.

The architecture already agrees: **argv is a non-JSON encoding of the same
contract** — flag strings lifted back to typed values by the coercion layer —
and completion answers are a third. The fabric was never "JSON-based"; JSON
is the document transport's choice, made in footman for local reasons
(stdlib-only, 30 ms manifest reads) that constrain one implementation, not
the design.

The precise requirement on an encoding is **round-trip fidelity against the
contract**: every value shape the contract admits survives encode→decode
unchanged, and refusal semantics are preserved. Fidelity can be *low* if the
binder can recover — argv is the most lossy encoding imaginable (strings
only, no nesting) and works because schema-directed coercion reconstructs the
values. That generalises: since both ends hold the contract, encodings split
into **self-describing** (JSON, CBOR — parseable without the schema) and
**schema-directed** (protobuf-style — compact because the schema supplies the
structure), and the manifest being fetchable is exactly what makes
schema-directed encodings legal for hot paths and a fleet rung.

Two places the choice stops being a detail:

- **The naming layer needs canonical bytes, and JSON is bad at it.**
  "Arguments in normal form" and content addressing require that the same
  value has the same bytes everywhere. JSON has no canonical form by default
  (key order, number representation; RFC 8785/JCS exists precisely because of
  this), while CBOR's deterministic-encoding rules are in its core spec
  (RFC 8949 §4.2). Clean split: transports encode however they negotiate;
  **the naming/addressing layer has exactly one canonical encoding**, used
  only for keys and hashes, never required on the wire. Many mouths, one
  spelling in the ledger — the cache-key/address distinction one layer down.
- **The artifact tier should not pass through a structured encoding at all.**
  Bytes-tier results want streaming and zero-copy — content-addressed byte
  streams with metadata in the envelope, the way git separates loose objects
  from refs. Large structured data has encodings whose whole point is the
  memory layout (Arrow); "represents the schema" there includes representing
  it *without deserialising*.

For the ecosystem, the mature pattern is HTTP's: **one mandatory-to-implement
encoding plus negotiation upward.** Mandate JSON as the floor — every
endpoint can always talk, humans can always debug by eye — and let endpoints
that both advertise CBOR or a schema-directed binary upgrade, the
advertisement riding the manifest or the handshake. LSP mandates JSON and
conquered the world; gRPC mandates protobuf and did fine; neither made the
floor optional.

The tax nobody prices in: encodings multiply the drift surface — N encodings
× M transports × L frontends is a compatibility matrix whose optional cells
rot silently. The answer is the same law extended once more: the conformance
kit's golden call/response pairs run *per encoding*, so "same call, same
answer" holds across encodings exactly as across transports.

---

## Security, decomposed

The part Willem was least clear on. It stops feeling foggy once it is four
separable problems rather than one blob.

### 1. Inbound — who may invoke a name

The principled answer is **capabilities, not identity**: possession of an
unforgeable reference *is* the authorization. Two reasons it fits unusually
well:

- **The confused deputy is the default topology.** A task calling a task, a
  daemon serving many invocations, an agent acting for a user — every
  interesting call is made by an intermediary holding someone else's
  authority. ACLs ask "who is calling?" and the deputy answers with its own
  identity, which is precisely the bug. Capabilities make authority a value
  that travels with the call.
- **The local rungs get capabilities almost free.** A unix socket behind
  filesystem permissions is a weak capability; a file descriptor passed over
  `SCM_RIGHTS` is a real one — unforgeable, unnameable, revoked by closing.
  The endpoint result kind *is* a capability, and yielding it to a dependent
  is delegation. The OS provides the primitive.

Identity re-enters only at the remote rung, where someone must decide which
capabilities to mint for which network principal — and the services note's
precondition stays right generalised: authorization is delegated, never
implemented; the fabric moves capabilities, it does not run an identity
provider.

Revocation, the known ocap weak spot, is answered by machinery already
designed: a capability is a **lease**, and the scope ladder's rungs
(subtree/run/project) are lease lifetimes. Nothing outlives its rung.

### 2. Outbound — what an invocation may do

Today a task body runs with the caller's full ambient authority. Fine for a
local task runner; fatal for a fabric, where an inbound call across a trust
boundary lands in a body holding *your* authority.

The fix is the same single gate the caching, remote, and addressing threads
all queue behind: **declared inputs and outputs** — plus the observation that
such a declaration *is a sandbox profile* (Bazel's sandboxing works exactly
this way: declared inputs are mounted, nothing else exists, undeclared reads
fail). The one missing declaration now gates a **fourth** goal, and the
security payoff may be the strongest of the four: it converts "trust this
body" into "this body physically cannot touch what it did not declare."

The effect vocabulary doubles as the permission vocabulary: derivations need
no authorization beyond read-caps on their inputs; actions are the only kind
needing authorization (and footman's confirm gates are already the human rung
of that, reframed); resources mint capabilities, so their question is who the
endpoint gets delegated to.

### 3. Results — trusting bytes you did not compute

The honest limit of content addressing. It verifies bytes-match-name only when
the name *is* the hash of the bytes — the fixed-output escape hatch. Most
names in the address space are **derivation names**: "task + args + input
fingerprint", which is a *claim* — "running this produces these bytes" — and
no hash check verifies a claim. The menu, the same one every build system
picks from:

1. **re-execute** — verification by redundancy; only meaningful for
   deterministic tasks, which is why determinism went load-bearing in the
   address-space note
2. **signed attestation** — trust whoever computed it, via signature (Nix
   binary-cache keys, SLSA provenance); identity reintroduced, but for
   artifact *producers*, a much smaller set than callers
3. **trust the store** — Bazel remote execution's answer; fine inside one
   org, not a fabric answer

So the address-space note's open question 4 ("trust between machines, or
verification only?") resolves to: verification only for fixed-output names,
attestation for derivation names, re-execution as the audit that keeps
attesters honest. Content addressing shrinks the trusted base; it cannot
empty it.

### 4. The reflective layer — where the agent rung makes it urgent

A manifest was already untrusted input driving parser, completion, and help —
name shadowing was caught in the services note. The agent transport escalates
it categorically: **for an LLM caller, tool descriptions are instructions, not
data.** A manifest does not just describe calls to an agent; it steers the
agent. This is the known MCP wound: description-borne prompt injection, and
"rug pulls" where a description mutates after approval.

The mitigations are update-security, a solved-shape problem: sign manifests
(TUF is the state of the art for "fetch descriptions of runnable things
without getting owned"), and **pin descriptions in a lockfile, refusing on
silent change** — trust-on-first-use plus diff-on-change, entirely aligned
with how manifests are already cached.

One composite rule is non-optional here — Willison's **lethal trifecta**:
private-data access + untrusted content + an exfiltration channel, held at
once, is game over regardless of per-call security. A capability-holding
scheduler is the first thing in the stack that can *see* all three legs:
effect-typed tools say which capabilities a session holds, manifest provenance
says which content is untrusted, and actions are enumerable. "This session
holds read-caps on private data and has ingested an unsigned manifest,
therefore actions require a human gate" is mechanically checkable. No agent
framework today can do this, because none of them type effects.

The stance tying all four together is the Waldo move again: **ambient
authority is to security what location transparency was to distribution** —
the convenient fiction that kills the system at scale. Make authority explicit
as values: calls carry capabilities, results carry provenance, manifests carry
signatures, effects are typed. The trust ladder stops being descriptive and
becomes enforced.

---

## Attenuation — the graveyard, and why this shape has an opening

Least authority demands every delegation hand over *less* than the delegator
holds — so attenuation must happen **at every call site**, which makes its
ergonomics the whole game. A mechanism that costs effort per call loses to
ambient authority every time, because ambient authority costs nothing and
works. Nobody has made it ergonomic; it is arguably why ocap systems stay
niche. The graveyard, one lesson per grave:

| attempt | mechanism | lesson |
|---|---|---|
| E-lang proxies, caretakers, membranes | attenuation as handwritten forwarder objects | attenuation-as-code is boilerplate at best; membranes (transitive wrapping) break identity and leak through return values — experts wrote papers about getting them right |
| rights bits (KeyKOS, seL4, Capsicum) | mask a fixed rights enum on a kernel object / fd | genuinely ergonomic — but only because the vocabulary is tiny and closed; there is no bitmask for "may deploy to staging but not prod" |
| tokens with caveats (macaroons, Biscuit, OAuth scopes) | append predicates anyone can add but no one can remove | attenuation shipped when it became *declarative data on the credential* — but the **verifier problem** (every acceptor must evaluate the predicate language) drove everyone back to flat scope strings |
| the powerbox (Plash, CapDesk, file pickers, Deno flags) | the user's existing gesture *is* the grant | the best attenuation syntax is no syntax — infer the grant from a gesture the caller was already making |
| policy-beside-code (Java SecurityManager, .NET CAS) | stack inspection + policy files | authority described in a separate artifact drifts from the code; everyone grants AllPermission to make the build pass; both deprecated and removed |

The pattern: attenuation shipped when it was **declarative, attached to
something that already existed, and evaluated by infrastructure** — and stayed
niche when it was imperative, a new object kind, and evaluated by user code.

### Why a typed-call fabric is unusually well-placed

Attenuation has been unergonomic because it never had a **place to live**. In
an untyped call graph authority is invisible, so restricting it means wrapping
live objects — membranes. This fabric has three properties that give it a
home, all existing for other reasons:

1. **The marker language is already a caveat language with an evaluator.**
   `between(1, 65535)`, `exists`, `check(…)` are predicates attached to a
   parameter, evaluated by the binder at the boundary, refusing before the
   body runs — precisely a macaroon caveat, with the verifier problem already
   solved because the fabric owns the binder on both ends of every transport.
   Attenuating a capability can be *the same act* as constraining a parameter.
2. **Arguments are the powerbox gesture.** If a `Path`-typed parameter
   *denotes a capability to that path* rather than a string the body combines
   with ambient authority, least authority becomes the default: the callee's
   world is its declared static inputs plus whatever its arguments name, and
   the caller hands less by passing less — which they were doing anyway. The
   typed binder is the only layer that knows which parameters denote
   world-references and which are plain values — the same distinction
   transport-relative markers already forced.
3. **The transport walls dissolve the membrane problem.** Across document,
   socket, and remote boundaries only serialisable data crosses; capabilities
   cross only as explicit, typed, manifest-visible values. Nothing transitive
   to wrap. Enforcement strength then tracks the trust ladder by itself:
   in-process it is advisory hygiene (no boundary to defend), at a subprocess
   it is sandbox-enforceable via the declared I/O set, at a socket or network
   it is fully mediated. *Declared everywhere, enforced exactly where trust
   changes* — the only honest offer any system can make, stated instead of
   fudged.

Delegation syntax nearly writes itself because the idiom exists: `.opts()` is
a per-reference override that weakens a declaration. A capability algebra
needs about four combinators — **narrow** (subset of operations/values),
**scope** (subset of a namespace), **expire** (a lease — the scope ladder
again), **count** (limited uses).

### What stays genuinely hard

- **Actions revert to the verifier problem.** The caveat vocabulary covers
  values and namespaces; "may spend under $100" is a domain predicate only the
  payment task can evaluate. Macaroon-style domain caveats, with a better home
  — the framework carries them, the plugin evaluates them.
- **Amplification through composition.** Two individually-safe capabilities
  held together can be dangerous (read-secrets + network is the trifecta
  again). No per-capability syntax catches it; only a holder-level policy in
  the scheduler can.
- **The human grant gesture.** A person delegating to an agent will not write
  combinators; the powerbox lesson says the grant must ride an existing
  gesture, and which gesture is unsolved by everyone currently shipping
  agents. A confirm prompt is the crude-but-honest placeholder.

---

## Design fiction — how it would feel

Scenes in a fictional `fab` CLI, deliberately not footman. Four author-facing
constructs total — world-denoting parameter types (`Reads`/`Writes`/
`Endpoint`), `narrow()` with marker-shaped caveats, `grant` reifying a
narrowing as a file, and the effect kind on `@task`. Everything else is
projection.

**The powerbox default — passing an argument is the grant:**

```python
@task
def minify(src: Reads[Path], out: Writes[Path]) -> None:
    out.write_text(minified(src.read_text()))
```

```console
$ fab minify src/app.js dist/app.min.js
FAIL  minify  (0.1s)
      denied: open ~/.ssh/id_ed25519 — not in this task's grant
      granted: read src/app.js, write dist/app.min.js
```

The refusal *names the grant*, because the grant is finally small enough to
print — the tell that least authority became the default.

**Attenuation as shrinking a parameter's domain**, reified as a file:

```python
@task(effect="action")
def deploy(env: Literal["staging", "prod"], ref: str = "HEAD") -> Receipt: ...

deploy.narrow(env=only("staging"))(ref=ref)   # this reference cannot say "prod"
```

```console
$ fab grant deploy --only env=staging --expires 7d --uses 20 > ci-deploy.cap
ci$ fab --cap ci-deploy.cap deploy prod
fab: refused — this capability narrows deploy to env=staging (expires in 6d, 19 uses left)
```

Exit 64, before the body runs, from the binder that was already refusing
out-of-range ints. `only("staging")` rode the marker rail; nothing new checks
it.

**The reflective layer respects the grant:**

```console
ci$ fab deploy <TAB>
staging
```

`prod` is not refused at TAB-time — it is simply *not offered*, because the
reflective transport projects the attenuated signature, not the author's.
Help, `--describe`, JSON schema, and the agent manifest all narrow the same
way; two views cannot disagree about what you may do, for the same reason two
transports cannot disagree about what a call means.

**Services — the yield is the delegation, honestly enforced:**

```python
@task(pre=[db])
def migrate(db: From[db]) -> None: ...

@task(pre=[db.narrow(readonly=True)])
def report(db: From[db]) -> None: ...
```

The fabric owns its own socket relay, so verb-level narrowing on *its*
protocol is enforced; `readonly` on raw TCP to postgres is a domain caveat it
can only carry — the plugin's attenuation hook maps it to a database role,
and `--describe` says which kind of enforcement is in play. The verifier
problem, placed rather than dodged.

**The agent seat**, where the effect vocabulary becomes the permission
surface:

```python
agent.mount(tasks, grant=[
    anything(effect="pure"),
    notify.narrow(channel=only("#dev")),
    deploy.narrow(env=only("staging")).gated(),   # legal, but each use asks a human
])
```

```console
$ fab --agent-log
14:03  fetch  manifest github.com/acme/tools  UNSIGNED
       policy: private-data + unsigned-content → actions now gated
14:07  agent requests: notify channel=#dev
       held for confirmation (trifecta policy) — approve? [y/N]
```

The trifecta check is mechanical because all three legs are typed: what the
session holds (capabilities), what it ingested (provenance), what it attempts
(an action).

**The receipt closes the loop** — authority appears in the `--json` envelope
exactly like timing does:

```json
{"task": "deploy", "args": {"env": "staging"}, "effect": "action",
 "cap": {"id": "ci-deploy", "narrowed": {"env": ["staging"]}, "uses_left": 18},
 "granted": ["net:deploy.acme.dev:443"], "denied": []}
```

No new object kinds, no policy files, no proxies: attenuation as data,
attached to artifacts that already exist, evaluated by infrastructure — the
graveyard lesson applied.

---

## Where it could go, stated maximally

A **typed, content-addressed call fabric**: every function anywhere
addressable by canonical name; callable over whichever transport is cheapest —
in-process if you are there, socket if it is resident, remote if it is
elsewhere, *not at all* if the result already has a name in a reachable store;
reflective to humans, editors, and agents from one contract; trust carried by
capabilities for invocation and content addresses plus attestation for data.
The build system, the task runner, the RPC framework, and the tool-discovery
layer stop being four products, because they were four views of one resolver
all along.

The pragmatic path is the one the neighbour notes already found: declared
inputs/outputs is the single gate (caching, remote, addressing, and now
sandboxing all queue behind it); each rung pays for itself locally before the
next exists; and the coherence property test is the law that keeps a growing
family of views honest.

## A name

> we're going to need a name for this fabric ;-) — Willem, 2026-08-17

"Typed call fabric" is a description, not a name. footman set the register —
the below-stairs household — and toolroom stayed in it, so the fabric's name
should too. Candidates, with what each carries:

- **livery** — the front-runner. Three metaphors for the price of one: the
  *uniform* a household servant wears, marking whose authority they act under
  (the capability story: you do not ask a footman for credentials, you read
  the livery); the London *livery companies* — chartered guilds, an ecosystem
  of trades under a shared institution (the conformance kit as charter); and
  the *livery stable* — hired transport. A capability grant is "wearing the
  livery"; attenuation is the livery saying which house and which duties.
- **loom** — what weaves threads into fabric; the transports are the threads.
  Short, strong, but a crowded namespace.
- **bellwire** — the wire from each room to the annunciator board in the
  servants' hall. The board *is* a household's address space; a pull is a
  typed request travelling a transport. The most literal fit for the
  transports frame, and the most obscure.
- **retinue** — the staff collectively; names the ecosystem rather than the
  mechanism.
- **parley** — a negotiation between parties under safe conduct; fits the
  envelope/handshake half, less the address space.

Whatever wins, one piece of sub-vocabulary is worth keeping from the same
register: the manifest a mounted tree presents is a **calling card** — the
thing a caller hands the footman at the door, announcing who they are and
what they may be received for. TOFU-plus-pinning is then exactly what a
card tray was for.

Willem's call, unhurried — nothing ships under this name until something of
it exists.

## What this note is not

- **Not a footman plan.** No phase list, no target release, no API proposal.
  If any of it ever lands, it lands through the existing threads (services,
  address space, incremental caching) on their own merits, not because this
  note exists.
- **Not a claim that footman should compete with Bazel, Ray, or MCP hosts.**
  The address-space note's "what I would not do" stands unrevised.
- **Not a security design.** It is a decomposition — the four problems and
  where each one's answer would live — written down so the next thread that
  touches trust starts from here instead of from fog.

## Open

1. **Does code identity eventually need content-addressing too** (Unison's
   answer)? Without it, "same name, two versions" haunts any fleet rung;
   with it, the neutral contract layer carries hashes as well as names.
2. **Is the neutral contract versioned per-distribution or per-signature?**
   The manifest-as-IDL move forces the question the moment a second frontend
   language exists.
3. **Attenuation for actions** — domain caveats need a placement story
   (framework carries, plugin evaluates) and a spelling. The `@health`
   decorator idiom from the services note is the likely shape.
4. **The human grant gesture** for delegating to agents. Unsolved everywhere;
   the confirm gate is the placeholder until a real gesture is found.
5. **Whether footman is ever the vehicle for any of this**, or stays the
   local proof that the shape works. A product decision, same as the remote
   rung's — and the same answer applies: deciding is cheapest before a
   launch, not after.
6. **The name.** Candidates above; livery is the recommendation. Willem's
   call.
7. **Which canonical encoding the naming layer uses** — JCS canonical JSON
   (stdlib-pure via a small implementation) or CBOR deterministic encoding
   (better spec, third-party dependency in Python). Only the keys-and-hashes
   layer cares; no wire format is constrained by the answer.
