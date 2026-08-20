"""The code sandbox, as this process reaches it: a URL and a token.

**A service, not a container this process runs.** `semantic/sandbox/` is the deployment, and it
is the third of three — Cube, the model store, the sandbox — so this package is a client beside
`cube/` and `modelstore/`, at the same tier and for the same reason.

Three things about it are worth knowing before changing anything here:

1. **It holds no credential.** No `api_secret` to sign a Cube token with, no warehouse password.
   So `run_query` and a Python interpreter being in the same hands is still safe: there is no
   route through the sandbox to a number that did not come through the four tiers.
2. **It can reach the network, and the container it replaced could not.** `--network none` cannot
   survive a service that answers HTTP. What that gives up is exfiltration: a script could send
   data it was already handed somewhere else. Accepted deliberately as the price of the portal
   running nothing — and the reason the token is required rather than optional.
3. **The workspace is the state, and it is on the far side.** A script's own files, an upload and
   its profile all live in the service's volume, and there is no table for any of them — a row
   could only ever be a second copy of `ls`. Deleting a chat asks the service to drop it.
"""

from .client import Run, SandboxClient, SandboxError, for_deployment, open_sandbox
from .profile import ColumnProfile, Profile, saved

__all__ = [
    "ColumnProfile",
    "Profile",
    "Run",
    "SandboxClient",
    "SandboxError",
    "for_deployment",
    "open_sandbox",
    "saved",
]
