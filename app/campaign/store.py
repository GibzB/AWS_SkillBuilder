"""Single-table DynamoDB campaign store client.

This module is the DynamoDB boundary for campaign and Campaign_Day items. It
wraps an injected low-level DynamoDB client (boto3 ``client("dynamodb")`` or a
fake in tests) behind a small :class:`DynamoDBClient` protocol so the rest of
the engine depends only on typed domain operations, never on boto3 directly.

Design references (see design.md, Data Models / Idempotency Design):

- Keys: PK ``CAMPAIGN#<campaign_id>``; SK ``META`` for the campaign, SK
  ``DAY#<zero-padded day>`` for days (Requirement 3.1, 3.2).
- Access patterns implemented here: GetItem (load campaign / day), Query
  (``begins_with DAY#`` ordered by day), PutItem (persist campaign), and
  UpdateItem with ``ConditionExpression`` asserting the expected current state,
  for both Campaign_State and Day_State transitions
  (Requirements 1.1, 1.7, 4.1, 5.13, 7.1, 15.1, 16.1, 16.2, 16.3, 16.4).
- Conditional PUBLISHED write ``day_state <> PUBLISHED`` mapping a blocked
  write to :class:`IdempotencyConflict` (Requirements 9.2, 9.4).
- Attempt-count increment via ``ADD attempt_count :one`` (Requirement 12.7).

The store speaks the DynamoDB low-level JSON attribute-value wire format
(``{"S": ...}``, ``{"N": ...}`` etc.) so it works against both the real client
and DynamoDB Local. Marshalling/unmarshalling is kept in one place and typed.
"""

from __future__ import annotations

from typing import Any, Protocol, cast

from app.campaign.models import (
    Campaign,
    CampaignDay,
    CampaignState,
    DayState,
    campaign_meta_sk,
    campaign_pk,
    day_number_from_sk,
    day_sk,
)
from app.common.errors import IdempotencyConflict, StateTransitionError

__all__ = ["DynamoDBClient", "CampaignStore"]

# Low-level DynamoDB attribute-value map, e.g. {"pk": {"S": "CAMPAIGN#abc"}}.
AttributeValue = dict[str, Any]
Item = dict[str, AttributeValue]


class DynamoDBClient(Protocol):
    """Minimal structural type for the injected boto3 DynamoDB client.

    Only the operations the store actually uses are declared. The real
    ``boto3.client("dynamodb")`` satisfies this protocol, as does an in-memory
    fake in tests. Methods accept and return the low-level attribute-value
    wire format.
    """

    def get_item(self, **kwargs: Any) -> dict[str, Any]:
        """Return an item by key (may omit ``Item`` when not found)."""
        ...

    def put_item(self, **kwargs: Any) -> dict[str, Any]:
        """Write an item, optionally guarded by a ConditionExpression."""
        ...

    def update_item(self, **kwargs: Any) -> dict[str, Any]:
        """Update attributes, optionally guarded by a ConditionExpression."""
        ...

    def query(self, **kwargs: Any) -> dict[str, Any]:
        """Query a partition, returning ``Items`` and optional pagination."""
        ...


def _is_conditional_check_failed(error: Exception) -> bool:
    """Return True if ``error`` is a DynamoDB ConditionalCheckFailedException.

    Matched structurally (by class name and boto3's ``response`` payload) so
    the store does not import botocore, and so an in-memory fake can raise a
    compatible error in tests.
    """
    if type(error).__name__ == "ConditionalCheckFailedException":
        return True
    response = getattr(error, "response", None)
    if isinstance(response, dict):
        code = response.get("Error", {}).get("Code")
        if code == "ConditionalCheckFailedException":
            return True
    return False


class CampaignStore:
    """Typed single-table store for Campaign and Campaign_Day items.

    Wraps an injected :class:`DynamoDBClient` and a table name (never
    hard-coded, always supplied by the caller). All marshalling to and from the
    DynamoDB wire format lives here so callers work purely in domain terms.
    """

    def __init__(self, client: DynamoDBClient, table_name: str) -> None:
        self._client = client
        self._table_name = table_name

    # ------------------------------------------------------------------
    # Campaign (META) operations
    # ------------------------------------------------------------------

    def get_campaign(self, campaign_id: str) -> Campaign | None:
        """Load the campaign META item, or None when it does not exist."""
        response = self._client.get_item(
            TableName=self._table_name,
            Key={
                "pk": {"S": campaign_pk(campaign_id)},
                "sk": {"S": campaign_meta_sk()},
            },
            ConsistentRead=True,
        )
        item = response.get("Item")
        if not item:
            return None
        return self._campaign_from_item(cast(Item, item))

    def put_campaign(self, campaign: Campaign) -> None:
        """Persist a campaign META item (PutItem, overwriting on same key)."""
        self._client.put_item(
            TableName=self._table_name,
            Item=self._campaign_to_item(campaign),
        )

    def update_campaign_state(
        self,
        campaign_id: str,
        expected_current: CampaignState,
        new_state: CampaignState,
        updated_at: str,
    ) -> None:
        """Transition a campaign's state guarded by its expected current state.

        Uses UpdateItem with ``ConditionExpression: campaign_state = :expected``
        so the write applies only when the campaign is in the state the caller
        expected. This both enforces the Campaign_State machine and provides
        idempotency for approve, run, pause, resume, and complete transitions
        (Requirements 16.1, 16.2; access pattern "Complete campaign",
        Requirement 15.1, 15.3). When the condition fails, the stored state is
        preserved and a :class:`StateTransitionError` naming the requested
        transition is raised.
        """
        try:
            self._client.update_item(
                TableName=self._table_name,
                Key={
                    "pk": {"S": campaign_pk(campaign_id)},
                    "sk": {"S": campaign_meta_sk()},
                },
                UpdateExpression="SET campaign_state = :new, updated_at = :updated_at",
                ConditionExpression="campaign_state = :expected",
                ExpressionAttributeValues={
                    ":new": {"S": new_state.value},
                    ":expected": {"S": expected_current.value},
                    ":updated_at": {"S": updated_at},
                },
            )
        except Exception as error:  # noqa: BLE001 - re-classified below
            if _is_conditional_check_failed(error):
                raise StateTransitionError(
                    current_state=expected_current.value,
                    requested_state=new_state.value,
                    scope="campaign",
                    message=(
                        f"Campaign '{campaign_id}' was not in expected state "
                        f"'{expected_current.value}' for transition to "
                        f"'{new_state.value}'"
                    ),
                ) from error
            raise

    # ------------------------------------------------------------------
    # Campaign_Day operations
    # ------------------------------------------------------------------

    def get_day(self, campaign_id: str, day: int) -> CampaignDay | None:
        """Load a single Campaign_Day item, or None when it does not exist."""
        response = self._client.get_item(
            TableName=self._table_name,
            Key={
                "pk": {"S": campaign_pk(campaign_id)},
                "sk": {"S": day_sk(day)},
            },
            ConsistentRead=True,
        )
        item = response.get("Item")
        if not item:
            return None
        return self._day_from_item(cast(Item, item))

    def put_day(self, day: CampaignDay) -> None:
        """Persist a Campaign_Day item (PutItem, overwriting on same key)."""
        self._client.put_item(
            TableName=self._table_name,
            Item=self._day_to_item(day),
        )

    def query_days(self, campaign_id: str) -> list[CampaignDay]:
        """Return all Campaign_Day items for a campaign, ordered by day.

        Uses a Query with ``begins_with(sk, "DAY#")`` scoped to the campaign
        partition. Because day sort keys are zero-padded, DynamoDB returns them
        in ascending day order (Requirements 3.1, 3.2). Pagination is followed
        so callers always receive the complete, ordered sequence.
        """
        days: list[CampaignDay] = []
        exclusive_start_key: Item | None = None
        while True:
            kwargs: dict[str, Any] = {
                "TableName": self._table_name,
                "KeyConditionExpression": "pk = :pk AND begins_with(sk, :day_prefix)",
                "ExpressionAttributeValues": {
                    ":pk": {"S": campaign_pk(campaign_id)},
                    ":day_prefix": {"S": "DAY#"},
                },
                "ScanIndexForward": True,
                "ConsistentRead": True,
            }
            if exclusive_start_key is not None:
                kwargs["ExclusiveStartKey"] = exclusive_start_key
            response = self._client.query(**kwargs)
            for raw in response.get("Items", []):
                days.append(self._day_from_item(cast(Item, raw)))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            exclusive_start_key = cast(Item, last_key)
        return days

    def update_day_state(
        self,
        campaign_id: str,
        day: int,
        expected_current: DayState,
        new_state: DayState,
        updated_at: str,
        last_error: str | None = None,
    ) -> None:
        """Transition a day's state guarded by its expected current state.

        Uses UpdateItem with ``ConditionExpression: day_state = :expected``,
        which both enforces the day state machine and provides idempotency
        (Requirements 4.1, 4.9, 5.13, 7.1, 16.3, 16.4). When the condition
        fails, the stored state is preserved and a :class:`StateTransitionError`
        naming the requested transition is raised.
        """
        set_parts = ["day_state = :new", "updated_at = :updated_at"]
        values: Item = {
            ":new": {"S": new_state.value},
            ":expected": {"S": expected_current.value},
            ":updated_at": {"S": updated_at},
        }
        if last_error is not None:
            set_parts.append("last_error = :last_error")
            values[":last_error"] = {"S": last_error}

        try:
            self._client.update_item(
                TableName=self._table_name,
                Key={
                    "pk": {"S": campaign_pk(campaign_id)},
                    "sk": {"S": day_sk(day)},
                },
                UpdateExpression="SET " + ", ".join(set_parts),
                ConditionExpression="day_state = :expected",
                ExpressionAttributeValues=values,
            )
        except Exception as error:  # noqa: BLE001 - re-classified below
            if _is_conditional_check_failed(error):
                raise StateTransitionError(
                    current_state=expected_current.value,
                    requested_state=new_state.value,
                    scope="day",
                    message=(
                        f"Day {day} of campaign '{campaign_id}' was not in "
                        f"expected state '{expected_current.value}' for "
                        f"transition to '{new_state.value}'"
                    ),
                ) from error
            raise

    def mark_published(
        self,
        campaign_id: str,
        day: int,
        publication_url: str,
        published_at: str,
        updated_at: str,
    ) -> None:
        """Conditionally mark a day PUBLISHED, guarding against duplicates.

        Uses UpdateItem with ``ConditionExpression: day_state <> :published``
        so the write succeeds only if the day is not already PUBLISHED
        (Requirements 9.2, 9.4). When the day is already PUBLISHED the
        conditional check fails and this raises :class:`IdempotencyConflict`,
        which the orchestrator treats as a prevented duplicate (no retry).
        """
        try:
            self._client.update_item(
                TableName=self._table_name,
                Key={
                    "pk": {"S": campaign_pk(campaign_id)},
                    "sk": {"S": day_sk(day)},
                },
                UpdateExpression=(
                    "SET day_state = :published, publication_url = :url, "
                    "published_at = :published_at, updated_at = :updated_at"
                ),
                ConditionExpression="day_state <> :published",
                ExpressionAttributeValues={
                    ":published": {"S": DayState.PUBLISHED.value},
                    ":url": {"S": publication_url},
                    ":published_at": {"S": published_at},
                    ":updated_at": {"S": updated_at},
                },
            )
        except Exception as error:  # noqa: BLE001 - re-classified below
            if _is_conditional_check_failed(error):
                raise IdempotencyConflict(
                    message=(
                        f"Day {day} of campaign '{campaign_id}' is already "
                        f"PUBLISHED; publish prevented as a duplicate"
                    ),
                    campaign_id=campaign_id,
                    day=day,
                ) from error
            raise

    def increment_attempt_count(
        self, campaign_id: str, day: int, updated_at: str
    ) -> int:
        """Increment a day's attempt count by 1 and return the new value.

        Uses UpdateItem ``ADD attempt_count :one`` before each attempt
        (Requirement 12.7). ``ReturnValues="UPDATED_NEW"`` yields the post-add
        count so the caller can enforce the maximum-attempt bound.
        """
        response = self._client.update_item(
            TableName=self._table_name,
            Key={
                "pk": {"S": campaign_pk(campaign_id)},
                "sk": {"S": day_sk(day)},
            },
            UpdateExpression="ADD attempt_count :one SET updated_at = :updated_at",
            ExpressionAttributeValues={
                ":one": {"N": "1"},
                ":updated_at": {"S": updated_at},
            },
            ReturnValues="UPDATED_NEW",
        )
        attributes = response.get("Attributes", {})
        raw = attributes.get("attempt_count", {}).get("N")
        return int(raw) if raw is not None else 0

    # ------------------------------------------------------------------
    # Marshalling helpers
    # ------------------------------------------------------------------

    def _campaign_to_item(self, campaign: Campaign) -> Item:
        item: Item = {
            "pk": {"S": campaign_pk(campaign.campaign_id)},
            "sk": {"S": campaign_meta_sk()},
            "campaign_id": {"S": campaign.campaign_id},
            "name": {"S": campaign.name},
            "description": {"S": campaign.description},
            "campaign_state": {"S": campaign.campaign_state.value},
            "total_days": {"N": str(campaign.total_days)},
            "timezone": {"S": campaign.timezone},
            "start_date": {"S": campaign.start_date},
            "execution_time": {"S": campaign.execution_time},
            "content_style": {"S": campaign.content_style},
            "notification_email": {"S": campaign.notification_email},
            "publisher_selection": {"S": campaign.publisher_selection},
            "created_at": {"S": campaign.created_at},
            "updated_at": {"S": campaign.updated_at},
        }
        if campaign.notification_phone is not None:
            item["notification_phone"] = {"S": campaign.notification_phone}
        return item

    def _campaign_from_item(self, item: Item) -> Campaign:
        phone_attr = item.get("notification_phone")
        notification_phone = (
            self._get_s(phone_attr) if phone_attr is not None else None
        )
        return Campaign(
            campaign_id=self._get_s(item["campaign_id"]),
            name=self._get_s(item["name"]),
            description=self._get_s(item.get("description")),
            campaign_state=CampaignState(self._get_s(item["campaign_state"])),
            total_days=self._get_n(item["total_days"]),
            timezone=self._get_s(item["timezone"]),
            start_date=self._get_s(item["start_date"]),
            execution_time=self._get_s(item["execution_time"]),
            content_style=self._get_s(item.get("content_style")),
            notification_email=self._get_s(item.get("notification_email")),
            notification_phone=notification_phone,
            publisher_selection=self._get_s(item.get("publisher_selection")),
            created_at=self._get_s(item["created_at"]),
            updated_at=self._get_s(item["updated_at"]),
        )

    def _day_to_item(self, day: CampaignDay) -> Item:
        item: Item = {
            "pk": {"S": campaign_pk(day.campaign_id)},
            "sk": {"S": day_sk(day.day)},
            "campaign_id": {"S": day.campaign_id},
            "day": {"N": str(day.day)},
            "day_state": {"S": day.day_state.value},
            "topic": {"S": day.topic},
            "article_id": {"S": day.article_id},
            "title": {"S": day.title},
            "description": {"S": day.description},
            "body_markdown": {"S": day.body_markdown},
            "publication_url": {"S": day.publication_url},
            "attempt_count": {"N": str(day.attempt_count)},
            "last_error": {"S": day.last_error},
            "idempotency_key": {"S": day.idempotency_key},
            "correlation_id": {"S": day.correlation_id},
            "updated_at": {"S": day.updated_at},
        }
        # Tags: use a DynamoDB string set only when non-empty (SS may not be
        # empty); otherwise store an empty list to keep the attribute present.
        if day.tags:
            item["tags"] = {"SS": list(day.tags)}
        else:
            item["tags"] = {"L": []}
        if day.published_at is not None:
            item["published_at"] = {"S": day.published_at}
        return item

    def _day_from_item(self, item: Item) -> CampaignDay:
        published_attr = item.get("published_at")
        published_at = (
            self._get_s(published_attr) if published_attr is not None else None
        )
        return CampaignDay(
            campaign_id=self._get_s(item["campaign_id"]),
            day=self._get_n(item["day"]),
            day_state=DayState(self._get_s(item["day_state"])),
            topic=self._get_s(item.get("topic")),
            article_id=self._get_s(item.get("article_id")),
            title=self._get_s(item.get("title")),
            description=self._get_s(item.get("description")),
            body_markdown=self._get_s(item.get("body_markdown")),
            tags=self._get_tags(item.get("tags")),
            publication_url=self._get_s(item.get("publication_url")),
            published_at=published_at,
            attempt_count=self._get_n(item.get("attempt_count"), default=0),
            last_error=self._get_s(item.get("last_error")),
            idempotency_key=self._get_s(item.get("idempotency_key")),
            correlation_id=self._get_s(item.get("correlation_id")),
            updated_at=self._get_s(item["updated_at"]),
        )

    @staticmethod
    def _get_s(attr: AttributeValue | None) -> str:
        """Read a string attribute value, defaulting to empty when absent."""
        if attr is None:
            return ""
        value = attr.get("S")
        return value if isinstance(value, str) else ""

    @staticmethod
    def _get_n(attr: AttributeValue | None, default: int = 0) -> int:
        """Read a numeric attribute value as int, defaulting when absent."""
        if attr is None:
            return default
        value = attr.get("N")
        return int(value) if value is not None else default

    @staticmethod
    def _get_tags(attr: AttributeValue | None) -> tuple[str, ...]:
        """Read the tags attribute, tolerating both string-set and list forms."""
        if attr is None:
            return ()
        string_set = attr.get("SS")
        if isinstance(string_set, list):
            return tuple(str(tag) for tag in string_set)
        list_value = attr.get("L")
        if isinstance(list_value, list):
            return tuple(
                str(entry["S"])
                for entry in list_value
                if isinstance(entry, dict) and "S" in entry
            )
        return ()
