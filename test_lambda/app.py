import logging
from datetime import datetime, timezone
from typing import Any


LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.INFO)


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    executed_at = datetime.now(timezone.utc).isoformat()
    LOGGER.info("Test schedule Lambda invoked: request_id=%s executed_at=%s",
                context.aws_request_id, executed_at)
    return {
        "message": "test-schedule-lambda-dynScheduler invoked successfully",
        "request_id": context.aws_request_id,
        "executed_at": executed_at,
        "received_event": event,
    }
