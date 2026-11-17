import calendar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


@dataclass(frozen=True)
class Rule:
    target_weekday: int
    anchor_ordinal: int
    anchor_weekday: int


def validate_rule(value: object) -> Rule:
    fields = {"target_weekday", "relationship", "anchor_ordinal", "anchor_weekday"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("Model response must contain exactly the supported rule fields")
    if value["relationship"] != "strictly_after":
        raise ValueError("Only the strictly_after relationship is supported")
    ordinal = value["anchor_ordinal"]
    if type(ordinal) is not int or not 1 <= ordinal <= 5:
        raise ValueError("anchor_ordinal must be an integer from 1 through 5")
    if value["target_weekday"] not in WEEKDAYS or value["anchor_weekday"] not in WEEKDAYS:
        raise ValueError("Weekdays must be full English weekday names")
    return Rule(
        WEEKDAYS.index(value["target_weekday"]),
        ordinal,
        WEEKDAYS.index(value["anchor_weekday"]),
    )


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


def resolve_date(rule: Rule, execution_time: str, zone: ZoneInfo, reference: datetime) -> datetime:
    if reference.tzinfo is None:
        raise ValueError("Reference timestamp must include a timezone")
    clock = datetime.strptime(execution_time, "%H:%M:%S").time()
    local = reference.astimezone(zone)
    year, month = local.year, local.month
    while True:
        first = datetime(year, month, 1)
        day = 1 + (rule.anchor_weekday - first.weekday()) % 7 + 7 * (rule.anchor_ordinal - 1)
        if day > calendar.monthrange(year, month)[1]:
            raise ValueError("Requested ordinal weekday does not exist in the anchor month")
        offset = (rule.target_weekday - rule.anchor_weekday) % 7 or 7
        date = first.replace(day=day) + timedelta(days=offset)
        candidate = local_timestamp(datetime.combine(date.date(), clock), zone)
        if candidate.astimezone(timezone.utc) > reference.astimezone(timezone.utc):
            return candidate
        if year == 9999 and month == 12:
            raise ValueError("No representable future occurrence")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
