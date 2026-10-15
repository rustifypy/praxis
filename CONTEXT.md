# Praxis

The portal answers questions about one connected database through a semantic layer it does not
run. Everything it queries, or stores a model in, sits behind an address someone else started.

This is the glossary. Implementation guidance lives in `AGENTS.md`; nothing here should describe
how anything works.

<!-- The docs site's glossary page includes everything from here on. --8<-- [start:terms] -->

## Language

### What a deployment is

**Deployment**:
The one connected database, together with the addresses that serve it. There is exactly one.
_Avoid_: source, connection, environment, instance

**Semantic deployment**:
The model store and the Cube behind it, reached at a single address. Cube has no address of its
own.
_Avoid_: Cube, the cube service, the semantic service

**Store**:
The model store: owner of the data model's files and their history, and the only door to Cube.
_Avoid_: repo, model service, registry

The word points at two sizes of thing, and both are correct. **A Store** is the service — one of
three containers in a **Semantic deployment**, the one holding the model. **`modelstore`** is what
this side calls everything reached through it: the client, the served model, and the generating and
editing of it. So the deployment is addressed as `semantic` (the wire key, the compose file, the
`GET /api/onboarding/deployment/semantic/compose` route) while the package is `modelstore`.
Neither name is wrong; expecting one word to mean one size of thing is.

**Sandbox**:
Where the agent runs Python. Optional to the code, required by onboarding.
_Avoid_: runner, executor, scratch

**Contract**:
The integer a deployment and the portal must agree on to speak to each other.
_Avoid_: version — it says whether two builds are compatible, not which build either is

### Conditions of a deployment

Four separate questions. Conflating them is the mistake this vocabulary exists to prevent.

**Deployed**:
An address has been stored. Says nothing about whether anything answers there.
_Avoid_: running, started, stopped, connected — the portal starts nothing, so it can never
observe a stop

**Onboarded**:
Setup finished: both addresses stored **and** a model in the store. A different question from
**Deployed**, because neither the sandbox address nor the model is part of that one. Setup's last
step is what builds the model, so two answering addresses and an empty store is the state setup is
*entered* in.
_Avoid_: configured, ready, complete

**Reachable**:
The address answers, and what answers is the thing we think it is — the right kind of service, a
matching **Contract**, and a token it accepts. Asked about an address someone just pasted.
_Avoid_: healthy, up, alive — each is vague enough to absorb the other three conditions, which
is how they came to be conflated

**Serving**:
What Cube is compiling and exposing right now. The truth about the model, as against what the
portal once did to it.
_Avoid_: built, compiled, live, deployed

### The model

**Ref**:
A named version of the data model, compiled and served independently of every other. **Branch** is
the word the UI uses for one, and the two are the same thing — the glossary says both rather than
contradicting the screen.
_Avoid_: branch *in code*. A local git branch is a different thing, and handing one to something
expecting a **Ref** is a live bug rather than a synonym

**Candidate**:
The throwaway **Ref** a rebuilt model lands on, so it can be compiled and refused without
touching what is **Serving**.

**Preview**:
The throwaway **Ref** an *unsaved* change set is compiled on, so Save can ask Cube a question
without committing anything. Created from the branch being edited and deleted immediately.
_Avoid_: **Candidate** — that one is a rebuild landing, this one is a question being asked

**Draft**:
A **Ref**'s model files on this machine while they are being edited, beside a copy of what the
store had when they were last synced — its **Origin**. A draft holds no history: the store owns
every commit, so a draft is a working copy and never a second source of truth.
_Avoid_: workspace, working tree, checkout, local branch, clone — there is no git on this side

**Origin**:
What the store held for a **Draft**'s **Ref** at the revision it was last synced from. What makes
an edit reversible and staleness a comparison rather than a guess.
_Avoid_: base, head, remote — the first is the browser's old word for it, the last two imply git

**Publish**:
Sending a **Draft**'s changes to its **Ref** as one commit, and landing it. This is the deploy.
_Avoid_: push — nothing is pushed anywhere, the store is asked over HTTP; save, sync, commit —
the first two happen on this machine only and the third is what the store does when asked

**Unsaved**:
Text typed into the editor and not yet written to the **Draft**. It exists in one tab and nowhere
else; a reload discards it, and nothing else does.
_Avoid_: staged — the browser held a checked-but-uncommitted set under that name until the
**Draft** replaced it, and reusing the word would name two different states

**Proposal**:
A model file the editor agent wants written, waiting for a person to accept it. It is on no
**Ref** — the agent commits nothing.
_Avoid_: edit, change — both imply it has happened

### Flows

A sequential pipeline someone repeats, defined by a folder rather than by code. The words here
are deliberately not the words the model side already uses: almost every obvious one is taken,
and reusing it would name two different things.

**Flow**:
The folder defining one pipeline: its numbered **Phases**, the prose instructing each, and the
reference material they share. The folder *is* the definition — nothing else describes a Flow,
and editing the files is how a Flow is changed.
_Avoid_: pipeline — that is the model build (`/api/onboarding/pipeline`); workspace — that is a
**Draft**'s tree; workflow, which is only a longer way of saying the same thing

`flows/` names two sizes of thing, and both are correct — the same way **Store** does. `flows/`
in the launch directory is the *content*: the Flow folders someone authors, edits and commits.
`praxis.flows` is the *engine* that reads them. Neither name is wrong; expecting one word
to mean one size of thing is.

**Phase**:
One step of a **Flow**, and one numbered folder in it. Either an agent turn with a scoped prompt
and toolset, or a single Python file run in the sandbox with no model call at all.
_Avoid_: stage — `modelgen` has four and `workspace.stage` is a function; step — those are
`modelgen`'s step rows; node, task

**Run**:
One execution of a **Flow**, and one folder under it holding everything that execution produced.
A Run outlives the request that started it: it pauses at a **Gate** and is resumed later.
_Avoid_: job — `core.jobs` is a different mechanism holding a machine-wide lock, and a Run must
never take it; execution, invocation

**Gate**:
The pause after a **Phase** where a person reads what landed and edits it before the next Phase
reads it. Required unless that Phase says otherwise. This is what a **Flow** is for.
_Avoid_: approval — that is the separate yes/no a script asks before it runs, and the two pause a
**Run** in structurally different places; checkpoint, breakpoint

**Brief**:
The free text a **Run** is started from, written into the Run's folder as its first **Working**
input. What varies between two Runs of one **Flow**; everything else about them is identical.
_Avoid_: prompt, question — a **Phase**'s prose is neither; parameters, which implies a form

**Reference**:
A **Phase** input belonging to the **Flow** and stable across every **Run** — voice, conventions,
domain rules. Resolves against the Flow's folder, and reaches the model inlined verbatim, because
it is meant to be read as a constraint.
_Avoid_: config, static, context

**Working**:
A **Phase** input this **Run** produced — an earlier Phase's output, or the **Brief**. Resolves
against the Run's folder, and reaches the model as text or as a shape, because it is meant to be
read as material.
_Avoid_: artifact on its own — both kinds are files; intermediate, temp

**Bundle**:
A named set of tools a **Phase** may ask for. The menu is closed, so a tool can be renamed
without breaking a file somebody wrote.
_Avoid_: toolset, scope, permissions

**Stale**:
A **Phase** whose output on disk came from an input that has since changed — an earlier Phase was
re-run, or a person edited what it read. The output is still there and still readable; what is
gone is the claim that it follows from what is above it.
_Avoid_: dirty, invalid, outdated — the first two say it is wrong, and it is not

<!-- --8<-- [end:terms] -->
