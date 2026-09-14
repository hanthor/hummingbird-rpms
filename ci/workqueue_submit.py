# Copyright Red Hat
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Submit file_actions to the workqueue service.

Shared module for CI scripts that need to create merge requests via the
workqueue-service instead of pushing branches directly.  Uses only
``urllib.request`` so there are no additional dependencies beyond the
standard library.
"""

import base64
import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

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
) -> dict[str, object]:
    """POST a work item to the workqueue service.

    Reads ``WORKQUEUE_SERVICE_URL`` and ``WORKQUEUE_OIDC_TOKEN`` from
    the environment (set via CI ``id_tokens`` and CI variables).

    Returns:
        Parsed JSON response body from the service.

    Raises:
        urllib.error.HTTPError: On non-2xx responses.
        KeyError: If required environment variables are missing.
    """
    url = os.environ["WORKQUEUE_SERVICE_URL"].rstrip("/") + "/api/work-items/mr"
    token = os.environ["WORKQUEUE_OIDC_TOKEN"]

    if not mr_description:
        mr_description = STANDING_JIRA_NOTE
    else:
        mr_description = f"{mr_description}\n\n{STANDING_JIRA_NOTE}"

    payload: dict[str, object] = {
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

    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )

    logger.info(
        "%s: submitting to workqueue (%d file action(s), %.1f KiB payload)",
        package,
        len(file_actions),
        len(data) / 1024,
    )

    try:
        with urllib.request.urlopen(req) as resp:
            body: dict[str, object] = json.loads(resp.read())
            status = resp.status
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        logger.error(
            "%s: workqueue submission failed (HTTP %d): %s",
            package,
            exc.code,
            detail,
        )
        raise

    if status == 201:
        logger.info("Created work item %s for %s", body.get("id"), package)
    elif status == 200:
        logger.info("Duplicate (idempotent) for %s: %s", package, body.get("id"))

    return body
