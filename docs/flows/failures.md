# What fails a Phase

A Flow is checked twice: when it is **written** (every save in the flow chat or the Edit tab, and
again before each run), and when a Phase **runs**. Everything that goes wrong at run time fails
the Phase and stops the run where it is. A run never continues on half the work, because the next
Phase would treat a half-made file as finished.

## Checked when the Flow is written

These stop a run from starting. The Flows list shows them, and the flow chat is told after every
write.

| Problem | Fix |
|---|---|
| `flow.yml` missing, not a mapping, or with an unknown field | Only `title` and `description` are allowed |
| `phases/` holds no `NN_name` folders | Add a Phase |
| A `phase.yml` field is unknown or has a wrong value | See [phase.yml reference](reference.md) |
| An agent Phase with no or empty `CONTEXT.md` | Write its instructions |
| A script Phase whose `script:` file is not there | Add the `.py` beside `phase.yml` |
| A prepared script or query that is not there | Add it, or remove it from the list |
| A prepared query that is not a Cube query | Fix the JSON so it parses as a Cube query |
| Two Phases with the same number | Renumber |
| A `reference:` that is missing or leaves the Flow folder | Fix the path |
| A `working:` that names neither `brief.md` nor a Phase folder | Use `brief.md` or `NN_name/output/<file>` |
| A `working:` from the same or a later Phase | A Phase can only read an earlier Phase |

**Warnings** do not stop a run: a Flow `CONTEXT.md` that is empty, a Phase with no outputs, a
Phase with prepared work that does not read `brief.md`, and (from `validate_flow`) a prepared query
with a relative `dateRange`.

`validate_flow` in the flow chat also compiles every prepared query against the served model.
That half needs the semantic deployment, so it is not run on every save.

## Checked when a Phase runs

| Message on the Run page | Cause | Fix |
|---|---|---|
| `input missing: …` | A declared input, prepared query or prepared script is not there | Usually a `working:` path that still names a renamed Phase folder |
| `declared output not produced: …` | The Phase ended without writing a declared file | Check the Phase's instructions; check the script wrote the plain filename |
| `declared output has no rows: …` | A declared `.csv`/`.tsv` holds only a header | Find out why no data matched; if no rows is a correct answer, add it to `may_be_empty` |
| `<script>.py exited <code>` | A script Phase's file failed | Read the error in the Phase's section; fix the script |
| `the phase's agent ended in an error` | The model hit its call limit (20), the `run_python` limit (8), or the LLM failed | Split the Phase, or prepare its work so it needs fewer steps |
| `stopped by the user` | Someone pressed Stop | — |
| `the phase stopped before it finished` | The stream was cut mid-Phase: the portal restarted, or the connection dropped | Re-run the Phase |
| `SandboxError: …` / `OSError: …` | The sandbox did not answer, or a file could not be written | See [Troubleshooting](../operations/troubleshooting.md) |

A script waiting for approval when the portal restarts fails its Phase: an approval is held in
memory and cannot survive a restart. A **Gate** is written to `run.json` and survives one.

## Re-running and Stale

Re-running a Phase marks every **done** Phase after it **Stale**: its output is still there and
still readable, but it no longer follows from what came before. Editing an output at a Gate does
the same. A failed run's sandbox workspace is kept so the half-written files can be inspected;
a run that ends well has its workspace removed.
