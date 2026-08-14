"""Code execution for the analysis agent: one container for the machine, one directory per chat.

Three invariants. The first two are enforced by shapes rather than by rules, because a deny
list is only as good as the next person who adds a parameter:

1. **The container holds no credential and reaches no network.** `container.argv` has no
   parameter that could carry a caller-supplied environment variable, a second mount or a
   network — `--network none` is not a default it can be talked out of. That is why there is no
   list of protected paths: `~/.analyst/analyst.db` is chmod 600 plaintext credentials and
   `.env` holds the Cube signing secret, and neither is *denied* so much as absent. There is
   one mount and it is `DATA_DIR/workspaces`.

2. **A path from the model is resolved against that chat's own directory, never trusted.**
   `scratch.resolve` is the only way a tool turns a string into a path, so no single tool can
   be the one that forgets. It checks the *resolved* target, which is what makes one check cover
   three escapes: `..`, an absolute path, and a symlink the sandbox planted itself — `bash` can
   point one at `/etc/passwd`, and a host-side read would otherwise follow it.

3. **A timeout has to kill the process, not the client.** `docker exec` is wrapped in the
   container's own `timeout`, because killing the docker client on the host leaves the command
   running inside. `proc.run`'s timeout is the backstop for a wedged client, not the mechanism.

Reads and writes are host-side against the bind mount, and only `bash` enters the container.
That keeps the file tools working while the image is still building, means they depend on
nothing installed inside it, and is what confines them to one chat's directory when the
container itself can see every chat's.

**One container is a deliberate choice with two consequences to know rather than discover.**
Its `--memory` and `--pids-limit` are shared, so a command that exhausts either kills the
container out from under every chat. That is survivable and not silent: the workspace is the
state, so nothing is lost, `ensure_up` builds a new container on the next command, and
`run_bash` says that is what happened rather than reporting an inscrutable failure. And `bash`
runs with the whole `workspaces` root mounted, so a shell in one chat can read another chat's
files — the file tools cannot, but a shell is a shell. Both are consistent with the threat
model this tool already has: one person, one machine, a token guard, and `analyst.db` holding
that person's own credentials in plaintext.
"""
