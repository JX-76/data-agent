"""DAG entrypoint status registry.

This module centralizes stability labels for DAG-related entrypoints so docs,
tests, and future refactors can reference one small source of truth.
"""

DAG_AGENT_STATUS = {
    "graph_agent": {
        "status": "stable",
        "role": "production DAG pipeline entrypoint",
        "recommended": True,
    },
    "dag_agent": {
        "status": "experimental",
        "role": "extension-heavy DAG aggregator",
        "recommended": False,
    },
}


def get_entrypoint_status(name: str) -> dict:
    """Return status metadata for a DAG-related entrypoint."""
    return DAG_AGENT_STATUS[name]


__all__ = ["DAG_AGENT_STATUS", "get_entrypoint_status"]
