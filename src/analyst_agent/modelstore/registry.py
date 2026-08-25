from ..core.deployment import Deployment


def label_of(deployment: Deployment) -> str:
    return deployment.credentials.get("CUBEJS_DB_NAME") or deployment.driver
