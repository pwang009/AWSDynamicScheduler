import time
from typing import Any

from botocore.exceptions import ClientError


class State:
    def __init__(self, table: Any):
        self.table = table

    def get(self, key: str) -> dict[str, Any]:
        return self.table.get_item(Key={"job_id": key}, ConsistentRead=True).get("Item", {})

    def acquire(self, key: str, owner: str) -> None:
        now = int(time.time())
        try:
            self.table.update_item(
                Key={"job_id": key},
                UpdateExpression="SET lock_owner = :owner, lock_expiry = :expiry",
                ConditionExpression="attribute_not_exists(lock_expiry) OR lock_expiry < :now",
                ExpressionAttributeValues={":owner": owner, ":expiry": now + 150, ":now": now},
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise RuntimeError("Schedule replacement is already in progress") from error
            raise

    def unlock(self, key: str, owner: str) -> None:
        self.table.update_item(
            Key={"job_id": key},
            UpdateExpression="REMOVE lock_owner, lock_expiry",
            ConditionExpression="lock_owner = :owner",
            ExpressionAttributeValues={":owner": owner},
        )

    def save_plan(self, key: str, plan: dict[str, str]) -> dict[str, str]:
        try:
            self.table.put_item(
                Item={"job_id": key, "plan": plan},
                ConditionExpression="attribute_not_exists(job_id)",
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
        return self.get(key)["plan"]

    def reserve(self, key: str, limit: int, prefix: str = "creation") -> int:
        now = int(time.time())
        try:
            response = self.table.update_item(
                Key={"job_id": key},
                UpdateExpression="SET #lease = :lease ADD #attempts :one",
                ConditionExpression=(
                    "(attribute_not_exists(#lease) OR #lease < :now) AND "
                    "(attribute_not_exists(#attempts) OR #attempts < :limit) AND "
                    "attribute_not_exists(#terminal)"
                ),
                ExpressionAttributeNames={
                    "#lease": f"{prefix}_lease",
                    "#attempts": f"{prefix}_attempts",
                    "#terminal": f"{prefix}_terminal",
                },
                ExpressionAttributeValues={
                    ":lease": now + 150,
                    ":now": now,
                    ":one": 1,
                    ":limit": limit,
                },
                ReturnValues="ALL_NEW",
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise RuntimeError(f"{prefix} attempt budget exhausted, terminal, or already in progress") from error
            raise
        return int(response["Attributes"][f"{prefix}_attempts"])

    def release(self, key: str, prefix: str = "creation", terminal: bool = False) -> None:
        expression = "SET #lease = :zero"
        names = {"#lease": f"{prefix}_lease"}
        values: dict[str, Any] = {":zero": 0}
        if terminal:
            expression += ", #terminal = :yes"
            names["#terminal"] = f"{prefix}_terminal"
            values[":yes"] = True
        self.table.update_item(
            Key={"job_id": key},
            UpdateExpression=expression,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )

    def record(self, key: str, field: str, value: str) -> None:
        self.table.update_item(
            Key={"job_id": key},
            UpdateExpression="SET #field = :value",
            ExpressionAttributeNames={"#field": field},
            ExpressionAttributeValues={":value": value},
        )
