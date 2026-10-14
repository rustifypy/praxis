# A Flow keeps its state in its folder, not in the database

Every other stateful thing in this portal is a row: chats, events, results, tiles, the deployment.
A **Flow** is not. Its definition, its **Runs**, each Run's event log, each Run's artifacts and the
record of where a Run is paused all live as plain files under `flows/` in the directory `analyst`
was launched from, or wherever `FLOWS_DIR` points. `core.db.SCHEMA` gains nothing.

Not `DATA_DIR`, which scopes the rest of the install. A Flow next to the code is a Flow in git,
and the prose that decides an agent's behaviour is then diffable and revertible like any other
source — which is the property the paper claims for the method and the only one a home under
`~/.analyst/` would have quietly withheld. The cost is real and accepted: flows found by relative
path are flows that depend on where the terminal was, and `FLOWS_DIR` is the answer for anyone who
would rather they did not.

This is the whole point of the feature rather than an implementation detail. The design follows
*Interpretable Context Methodology* (arXiv:2603.16021), whose claim is that for sequential
human-reviewed workflows the filesystem can do what a framework would otherwise do in code:
folder numbering is the execution order, one Phase's output folder is the next Phase's input, and
the state of a Run is what is on disk. A row would make every one of those a second source of
truth that can disagree with the files, and it would take away the properties the files are there
for — copy a Flow to hand it over, `ls` a Run to see what it produced, `git diff` a prompt to see
what changed about the pipeline's behaviour.

## Consequences

Three things follow that are easy to mistake for oversights.

`run_turn` takes its sink as an argument rather than always calling `transcript.append`. sqlite
stays the default and a chat is unaffected; a Run passes a writer for its own `events.jsonl`. The
alternative was a second copy of that loop, which this codebase has been burned by before.

A query in a Phase writes its rows to a CSV in the Phase's `output/` (`extract`) instead of
saving a `results` row. That is not a special case bolted on — a query's rows landing as a file
*is* the handoff to the next Phase.

The flow chat's scope is in `flows/<flow>/chat.json`, not a column on `chats`. There is no
migration mechanism here, so a new column would mean every existing user deletes `analyst.db` to
get this feature.

The one thing genuinely given up: a Run's state is only as durable as the files. An **approval**
pause lives on an in-memory queue inside a Phase and cannot be written down usefully, so a restart
while a script waits fails that Phase and it is re-run. A **Gate** pause, which is between Phases,
survives anything.
