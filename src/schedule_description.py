import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo


def validate_date(value: object) -> date:
    if not isinstance(value, dict) or set(value) != {"execution_date"}:
        raise ValueError("Model response must contain exactly execution_date")
    day = value["execution_date"]
    if not isinstance(day, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", day):
        raise ValueError("execution_date must be a YYYY-MM-DD string")
    return date.fromisoformat(day)


def local_timestamp(value: datetime, zone: ZoneInfo) -> datetime:
    candidates = []
    for fold in (0, 1):
        candidate = value.replace(tzinfo=zone, fold=fold)
        restored = candidate.astimezone(timezone.utc).astimezone(zone)
        if restored.replace(tzinfo=None) == value:
            candidates.append(candidate)
    if not candidates:
        raise ValueError("Execution time does not exist due to a timezone transition")
    if len({item.utcoffset() for item in candidates}) > 1:
        raise ValueError("Execution time is ambiguous due to a timezone transition")
    return candidates[0]


def resolve_date(day: date, execution_time: str, zone: ZoneInfo, reference: datetime) -> datetime:
    if reference.tzinfo is None:
        raise ValueError("Reference timestamp must include a timezone")
    clock = datetime.strptime(execution_time, "%H:%M:%S").time()
    candidate = local_timestamp(datetime.combine(day, clock), zone)
    if candidate.astimezone(timezone.utc) <= reference.astimezone(timezone.utc):
        raise ValueError("Model execution date and input time must be strictly in the future")
    return candidate
