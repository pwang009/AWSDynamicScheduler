import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)

from state import State


LOGGER = logging.getLogger(__name__)
TRANSIENT_CODES = {
    "ThrottlingException", "InternalServerException", "ServiceUnavailableException",
    "RequestTimeout", "RequestTimeoutException",
}
TRANSPORT_ERRORS = (
    ConnectionClosedError, ConnectTimeoutError, EndpointConnectionError, ReadTimeoutError,
)


def retryable(error: Exception) -> bool:
    return isinstance(error, TRANSPORT_ERRORS) or (
        isinstance(error, ClientError) and error.response["Error"]["Code"] in TRANSIENT_CODES
    )


def get_schedule(client: Any, request: dict[str, Any]) -> dict[str, Any] | None:
    try:
        existing = client.get_schedule(Name=request["Name"], GroupName=request["GroupName"])
    except ClientError as error:
        if error.response["Error"]["Code"] == "ResourceNotFoundException":
            return None
        raise
    return existing


def reconcile(existing: dict[str, Any] | None, request: dict[str, Any]) -> str | None:
    if existing is None or existing.get("Description") != request["Description"]:
        return None
    for field in ("ScheduleExpression", "ScheduleExpressionTimezone", "ActionAfterCompletion", "State"):
        if existing.get(field) != request[field]:
            raise ValueError(f"Existing schedule conflicts on {field}")
    if existing.get("FlexibleTimeWindow") != request["FlexibleTimeWindow"]:
        raise ValueError("Existing schedule conflicts on FlexibleTimeWindow")
    for field in ("Arn", "RoleArn", "Input"):
        if existing.get("Target", {}).get(field) != request["Target"][field]:
            raise ValueError(f"Existing schedule conflicts on Target.{field}")
    return existing["Arn"]


def create_schedule(
    client: Any, state: State, plan: dict[str, str], role_arn: str, group: str,
) -> dict[str, str]:
    lock_key = f"lock#{group}#{plan['schedule_name']}"
    owner = uuid.uuid4().hex
    state.acquire(lock_key, owner)
    try:
        return replace_schedule(client, state, plan, role_arn, group)
    finally:
        state.unlock(lock_key, owner)


def replace_schedule(
    client: Any, state: State, plan: dict[str, str], role_arn: str, group: str,
) -> dict[str, str]:
    key = f"schedule#{plan['job_id']}"
    request = {
        "Name": plan["schedule_name"],
        "GroupName": group,
        "ClientToken": plan["job_id"],
        "Description": f"dynamic-scheduler:{plan['job_id']}",
        "ScheduleExpression": f"at({plan['local_timestamp']})",
        "ScheduleExpressionTimezone": plan["timezone"],
        "FlexibleTimeWindow": {"Mode": "OFF"},
        "ActionAfterCompletion": "DELETE",
        "State": "ENABLED",
        "Target": {"Arn": plan["target_arn"], "RoleArn": role_arn, "Input": json.dumps({})},
    }

    def result(arn: str, status: str) -> dict[str, str]:
        state.record(key, "schedule_arn", arn)
        return {**plan, "schedule_arn": arn, "status": status, "state_key": key}

    while True:
        recorded = state.get(key)
        existing = get_schedule(client, request)
        arn = reconcile(existing, request)
        if arn:
            return result(arn, "already_exists")
        if recorded.get("schedule_arn"):
            raise RuntimeError(
                "Previously created schedule is missing or replaced; refusing to recreate it. "
                "Use a new request_id for an intentional rerun."
            )
        if datetime.fromisoformat(plan["execution_timestamp"]) <= datetime.now(timezone.utc):
            raise ValueError("Resolved execution timestamp has expired")
        attempt = state.reserve(key, 3)
        try:
            if existing is not None:
                LOGGER.info("Deleting existing schedule %s in group %s", request["Name"], group)
                try:
                    client.delete_schedule(
                        Name=request["Name"], GroupName=group, ClientToken=f"delete-{plan['job_id'][:57]}",
                    )
                except ClientError as error:
                    if error.response["Error"]["Code"] != "ResourceNotFoundException":
                        raise
            response = client.create_schedule(**request)
        except (ClientError, *TRANSPORT_ERRORS) as error:
            transient = retryable(error) or (
                isinstance(error, ClientError) and error.response["Error"]["Code"] == "ConflictException"
            )
            # A timeout or conflict can follow successful creation.
            if transient:
                arn = reconcile(get_schedule(client, request), request)
                if arn:
                    return result(arn, "created")
            state.release(key, terminal=not transient)
            LOGGER.warning("Schedule creation attempt %s failed: %s", attempt, error)
            if not transient or attempt >= 3:
                raise
            time.sleep(2 ** (attempt - 1))
        else:
            return result(response["ScheduleArn"], "created")
