"""Submit file_actions to the workqueue service.

Shared module for CI scripts that need to create merge requests via the
workqueue-service instead of pushing branches directly.
"""

import base64
import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
import sentry_sdk

sentry_sdk.init(auto_enabling_integrations=False)

logger = logging.getLogger("workqueue_submit")

# Standing work item for autonomous MRs (HUM-6140 / HUM-6146).
STANDING_JIRA_NOTE = "Jira: [HUM-6146](https://redhat.atlassian.net/browse/HUM-6146)"


def file_action(
    action: str,
    file_path: str,
    content_path: Path | None = None,
) -> dict[str, str]:
    """Build a single file_action dict for the workqueue API.

    Args:
        action: One of "create", "update", "delete", "move".
        file_path: Target path in the repository.
        content_path: Local file whose content will be base64-encoded.
            Required for create/update, must be omitted for delete.
    """
    fa: dict[str, str] = {"action": action, "file_path": file_path}
    if content_path is not None:
        fa["content"] = base64.b64encode(content_path.read_bytes()).decode()
    return fa


def submit(
    *,
    package: str,
    project: str,
    change_kind: str,
    commit_message: str,
    source_branch: str,
    mr_title: str,
    idempotency_key: str,
    file_actions: list[dict[str, str]],
    auto_merge: bool = False,
    draft: bool = False,
    labels: list[str] | None = None,
    mr_description: str = "",
    tickets: list[str] | None = None,
) -> dict[str, Any]:
    """POST a work item to the workqueue service.

    Reads ``WORKQUEUE_SERVICE_URL`` and ``WORKQUEUE_OIDC_TOKEN`` from
    the environment (set via CI ``id_tokens`` and CI variables).

    Returns:
        Parsed JSON response body from the service.

    Raises:
        requests.HTTPError: On non-2xx responses.
    """
    url = urljoin(os.environ["WORKQUEUE_SERVICE_URL"], "/api/work-items/mr")
    token = os.environ["WORKQUEUE_OIDC_TOKEN"]

    if not mr_description:
        mr_description = STANDING_JIRA_NOTE
    else:
        mr_description = f"{mr_description}\n\n{STANDING_JIRA_NOTE}"

    payload: dict[str, Any] = {
        "package": package,
        "project": project,
        "change_kind": change_kind,
        "commit_message": commit_message,
        "source_branch": source_branch,
        "target_branch": "main",
        "mr_title": mr_title,
        "idempotency_key": idempotency_key,
        "file_actions": file_actions,
        "auto_merge": auto_merge,
        "draft": draft,
        "labels": labels or [],
        "mr_description": mr_description,
        "tickets": tickets or [],
    }

    logger.info(
        "%s: submitting to workqueue (%d file action(s))",
        package,
        len(file_actions),
    )

    resp = requests.post(
        url,
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    resp.raise_for_status()
    body: dict[str, Any] = resp.json()

    if resp.status_code == 201:
        logger.info("Created work item %s for %s", body.get("id"), package)
    elif resp.status_code == 200:
        logger.info("Duplicate (idempotent) for %s: %s", package, body.get("id"))

    return body
