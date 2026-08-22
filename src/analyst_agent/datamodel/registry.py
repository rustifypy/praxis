from ..core.deployment import Deployment

GROUNDING_ROOT = "grounding"

# The grounding kinds a ref may carry. `Grounding` reads each into its own attribute, so this is
# the only place the set is enumerable.
ARTEFACT_DIRS = ("rules", "skills", "certified_queries")


def label_of(deployment: Deployment) -> str:
    return deployment.credentials.get("CUBEJS_DB_NAME") or deployment.driver
