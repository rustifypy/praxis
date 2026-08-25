# Analyst

The portal answers questions about one connected database through a semantic layer it does not
run. Everything it queries, or stores a model in, sits behind an address someone else started.

This is the glossary. Implementation guidance lives in `CLAUDE.md`; nothing here should describe
how anything works.

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
`GET /api/deployment/semantic/compose` route) while the package is `modelstore`. Neither name is
wrong; expecting one word to mean one size of thing is.

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

