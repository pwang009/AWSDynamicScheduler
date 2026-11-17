import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import boto3
from botocore.config import Config

from bedrock_parser import parse_description
from schedule_description import resolve_date
from scheduler import create_schedule
from state import State

# from notifications import send_creation_email


LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.INFO)
FIELDS = (
    "schedule_description", "target_lambda_name", "execution_time", "timezone",
    "bedrock_model_id", "bedrock_region", "schedule_name",
)


def validate_event(event: object) -> dict[str, str]:
    if not isinstance(event, dict) or set(event) - {*FIELDS, "scheduled_at"}:
        raise ValueError("Expected one description/target object with only supported fields")
    for field in FIELDS:
        if not isinstance(event.get(field), str) or not event[field].strip():
            raise ValueError(f"{field} must be a nonempty string")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", event["schedule_name"]):
        raise ValueError("schedule_name must be 1-64 letters, digits, underscores, dots, or hyphens")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", event["target_lambda_name"]):
        raise ValueError("Invalid target Lambda name")
    if not event["target_lambda_name"].endswith("-dynScheduler"):
        raise ValueError("Target Lambda name must end in -dynScheduler")
    if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]", event["execution_time"]):
        raise ValueError("execution_time must be a valid HH:MM:SS time")
    ZoneInfo(event["timezone"])
    if (
        event["bedrock_model_id"] != os.environ["ALLOWED_BEDROCK_MODEL_ID"]
        or event["bedrock_region"] != os.environ["ALLOWED_BEDROCK_REGION"]
    ):
        raise ValueError("Unapproved Bedrock model/Region pair")
    target_arn = os.environ["TARGET_LAMBDA_ARN"]
    if target_arn.split(":")[-1] != event["target_lambda_name"]:
        raise ValueError("Target Lambda does not match the deployment-approved ARN")
    if len(event["schedule_description"]) > 4000:
        raise ValueError("Schedule description exceeds 4000 characters")
    return {field: event[field] for field in FIELDS}


def lambda_handler(event: object, context: Any) -> dict[str, str]:
    inputs = validate_event(event)
    reference = datetime.now(timezone.utc)
    if isinstance(event, dict) and "scheduled_at" in event:
        scheduled_at = event["scheduled_at"]
        if not isinstance(scheduled_at, str):
            raise ValueError("scheduled_at must be an ISO timestamp")
        reference = datetime.fromisoformat(scheduled_at.replace("Z", "+00:00"))
        if reference.tzinfo is None or reference > datetime.now(timezone.utc):
            raise ValueError("scheduled_at must be timezone-aware and not in the future")
    month = reference.astimezone(ZoneInfo(inputs["timezone"])).strftime("%Y-%m")
    job = hashlib.sha256(json.dumps([inputs, month], sort_keys=True).encode()).hexdigest()
    config = Config(connect_timeout=3, read_timeout=20, retries={"total_max_attempts": 1})
    table = boto3.resource("dynamodb", config=config).Table(os.environ["STATE_TABLE"])
    state = State(table)
    saved = state.get(f"plan#{job}")
    if saved:
        plan = saved["plan"]
    else:
        client = boto3.client("bedrock-runtime", region_name=inputs["bedrock_region"], config=config)
        current_date = reference.astimezone(ZoneInfo(inputs["timezone"])).strftime("%m/%d/%Y")
        description = f"{inputs['schedule_description']}, current date: {current_date}"
        day = parse_description(
            client, inputs["bedrock_model_id"], description,
            reference, inputs["execution_time"], ZoneInfo(inputs["timezone"]),
        )
        execution = resolve_date(day, inputs["execution_time"], ZoneInfo(inputs["timezone"]), reference)
        plan = state.save_plan(f"plan#{job}", {
            **inputs,
            "job_id": job,
            "target_arn": os.environ["TARGET_LAMBDA_ARN"],
            "execution_date": execution.date().isoformat(),
            "execution_timestamp": execution.isoformat(),
            "local_timestamp": execution.strftime("%Y-%m-%dT%H:%M:%S"),
        })
    result = create_schedule(
        boto3.client("scheduler", config=config),
        state, plan, os.environ["SCHEDULER_ROLE_ARN"], os.environ["SCHEDULE_GROUP"],
    )
    # send_creation_email(result, state, boto3.client("sesv2", config=config))
    result["notification_status"] = "disabled"
    LOGGER.info("Schedule %s: %s; email disabled", result["schedule_name"], result["status"])
    return result
