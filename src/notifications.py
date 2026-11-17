import json
import logging
import os
import time
from typing import Any

from botocore.exceptions import ClientError

from scheduler import TRANSPORT_ERRORS, retryable
from state import State


LOGGER = logging.getLogger(__name__)


def email_configuration() -> tuple[str, list[str], int, float]:
    if os.environ["EMAIL_PROVIDER"] != "ses":
        raise ValueError("Only EMAIL_PROVIDER=ses is implemented")
    if os.environ["EMAIL_RECOVERY_POLICY"] != "manual":
        raise ValueError("Only EMAIL_RECOVERY_POLICY=manual is implemented")
    sender = os.environ["EMAIL_SENDER"]
    recipients = json.loads(os.environ["EMAIL_RECIPIENTS"])
    attempts = int(os.environ["EMAIL_MAX_ATTEMPTS"])
    delay = float(os.environ["EMAIL_RETRY_BASE_SECONDS"])
    if not sender or not isinstance(recipients, list) or not recipients:
        raise ValueError("Email sender and a nonempty JSON recipient array are required")
    if any(not isinstance(item, str) or not item.strip() for item in recipients):
        raise ValueError("Email recipients must be nonempty strings")
    if not 1 <= attempts <= 3 or not 0 <= delay <= 2:
        raise ValueError("Email attempts must be 1-3 and retry base seconds must be 0-2")
    return sender, recipients, attempts, delay


def send_creation_email(schedule: dict[str, str], state: State, client: Any) -> str:
    sender, recipients, limit, delay = email_configuration()
    key = schedule["state_key"]
    if message_id := state.get(key).get("email_message_id"):
        return message_id
    body = "\n".join(
        f"{field}: {schedule[field]}"
        for field in (
            "schedule_name", "schedule_arn", "target_lambda_name", "schedule_description",
            "execution_date", "execution_time", "timezone",
        )
    )
    while True:
        attempt = state.reserve(key, limit, prefix="email")
        state.record(key, "email_status", "pending")
        try:
            response = client.send_email(
                FromEmailAddress=sender,
                Destination={"ToAddresses": recipients},
                Content={"Simple": {
                    "Subject": {"Data": "Lambda schedule created", "Charset": "UTF-8"},
                    "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
                }},
            )
        except (ClientError, *TRANSPORT_ERRORS) as error:
            transient = retryable(error)
            state.release(key, prefix="email", terminal=not transient)
            LOGGER.error("Schedule exists, but email attempt %s failed: %s", attempt, error)
            if not transient or attempt >= limit:
                raise RuntimeError("Schedule exists; notification requires manual recovery") from error
            time.sleep(delay * 2 ** (attempt - 1))
        else:
            state.record(key, "email_message_id", response["MessageId"])
            state.record(key, "email_status", "sent")
            return response["MessageId"]
