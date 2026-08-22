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
Setup finished: both addresses stored. A different question from **Deployed**, because the
sandbox address is not part of it.
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
A named version of the data model, compiled and served independently of every other.
_Avoid_: branch — a local git branch is a different thing, and handing one to something
expecting a **Ref** is a live bug rather than a synonym

**Candidate**:
The throwaway **Ref** a rebuilt model lands on, so it can be compiled and refused without
touching what is **Serving**.

**Grounding**:
What the agent believes before the question is asked — versioned beside the model it describes.
_Avoid_: context, prompt, instructions
