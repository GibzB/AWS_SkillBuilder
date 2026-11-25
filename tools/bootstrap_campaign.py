#!/usr/bin/env python3
"""Bootstrap a campaign on whichever AWS account the active profile points at.

Creates the campaign, approves it, then invokes the orchestrator once per day so
every article is generated, validated, and published to S3/CloudFront. Safe to
re-run: use --skip-create to resume, and days already PUBLISHED are skipped.

    AWS_PROFILE=default python tools/bootstrap_campaign.py --email you@example.com
    AWS_PROFILE=default python tools/bootstrap_campaign.py --skip-create --days 21

News campaign (aws-news-30): --news creates and approves the campaign with
placeholder topics and generates nothing (one day is generated per scheduled
run, or pass --through N). --roundup [--week N] runs the weekly roundup once.

The email is passed in (never hardcoded) because this repo is public.
"""
from __future__ import annotations

import argparse
import datetime
import json
import time
from typing import Any

import boto3

from app.topics.planner import TopicPlanner


NEWS_CAMPAIGN = "aws-news-30"
NEWS_NAME = "30 Days of AWS News"
NEWS_DAYS = 30


def _news_topics(days: int) -> list[str]:
    """Placeholder topics; the real topic is chosen from the feed on the day."""
    return [
        f"News day {n}: topic chosen from the AWS What's New feed on the day"
        for n in range(1, days + 1)
    ]


def _roundup_event(campaign: str, week: int | None) -> dict[str, Any]:
    """The direct-invoke event for the weekly roundup (same shape as the schedule)."""
    event: dict[str, Any] = {
        "source": "eventbridge-scheduler",
        "campaign_id": campaign,
        "kind": "weekly_roundup",
    }
    if week is not None:
        event["week"] = week
    return event


def _ts() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _invoke(lam: Any, function: str, payload: dict[str, Any]) -> Any:
    resp = lam.invoke(FunctionName=function, Payload=json.dumps(payload).encode("utf-8"))
    body = resp["Payload"].read().decode("utf-8")
    try:
        return json.loads(body)
    except ValueError:
        return {"raw": body}


def _day_item(ddb: Any, table: str, campaign: str, day: int) -> dict[str, Any]:
    item: dict[str, Any] = ddb.get_item(
        TableName=table,
        Key={"pk": {"S": f"CAMPAIGN#{campaign}"}, "sk": {"S": f"DAY#{day:04d}"}},
    ).get("Item", {})
    return item


def _reset(ddb: Any, s3: Any, table: str, campaign: str, bucket: str) -> None:
    """Delete every item in the campaign partition and every published day page."""
    pk = {"S": f"CAMPAIGN#{campaign}"}
    deleted = 0
    kwargs: dict[str, Any] = {
        "TableName": table,
        "KeyConditionExpression": "pk = :pk",
        "ExpressionAttributeValues": {":pk": pk},
        "ProjectionExpression": "pk, sk",
    }
    while True:
        page = ddb.query(**kwargs)
        for item in page.get("Items", []):
            ddb.delete_item(TableName=table, Key={"pk": item["pk"], "sk": item["sk"]})
            deleted += 1
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    removed = 0
    token: str | None = None
    while True:
        listing = s3.list_objects_v2(
            Bucket=bucket, Prefix="day/", **({"ContinuationToken": token} if token else {})
        )
        for obj in listing.get("Contents", []):
            s3.delete_object(Bucket=bucket, Key=obj["Key"])
            removed += 1
        if not listing.get("IsTruncated"):
            break
        token = listing["NextContinuationToken"]
    print(f"reset  : deleted {deleted} DynamoDB item(s) and {removed} S3 page(s)")


def _rewind(ddb: Any, table: str, campaign: str, from_day: int, total: int) -> None:
    """Return days ``from_day..total`` to PLANNED with generated content cleared.

    The orchestrator always picks the lowest PLANNED/FAILED day, so rewinding the
    tail makes it regenerate exactly those days. The campaign is set back to
    RUNNING because publishing the last day had marked it COMPLETED.
    """
    pk = {"S": f"CAMPAIGN#{campaign}"}
    cleared = (
        "title, description, body_markdown, tags, publication_url, published_at, "
        "article_id, last_error"
    )
    for day in range(from_day, total + 1):
        ddb.update_item(
            TableName=table,
            Key={"pk": pk, "sk": {"S": f"DAY#{day:04d}"}},
            UpdateExpression=f"SET day_state = :p, attempt_count = :z REMOVE {cleared}",
            ConditionExpression="attribute_exists(pk)",
            ExpressionAttributeValues={":p": {"S": "PLANNED"}, ":z": {"N": "0"}},
        )
    ddb.update_item(
        TableName=table,
        Key={"pk": pk, "sk": {"S": "META"}},
        UpdateExpression="SET campaign_state = :r",
        ExpressionAttributeValues={":r": {"S": "RUNNING"}},
    )
    print(f"rewind : days {from_day}..{total} set to PLANNED; campaign set to RUNNING")


def _extend(ddb: Any, table: str, campaign: str, new_total: int, topics: list[str]) -> None:
    """Lengthen the campaign to ``new_total`` days.

    ``topics`` are the topics of the LAST ``len(topics)`` days, in day order. A day
    that already exists must still be PLANNED (its topic is replaced); days beyond
    the current length are appended as PLANNED. Published or in-flight days are
    never touched, and the campaign can only grow.
    """
    from dataclasses import replace

    from app.campaign.idempotency import derive_idempotency_key
    from app.campaign.models import CampaignDay, DayState
    from app.campaign.store import CampaignStore

    store = CampaignStore(ddb, table)
    current = store.get_campaign(campaign)
    if current is None:
        raise SystemExit(f"campaign {campaign!r} not found")
    if new_total < current.total_days:
        raise SystemExit(f"--extend-to {new_total} would shrink the campaign ({current.total_days} days)")
    first = new_total - len(topics) + 1
    if first > current.total_days + 1:
        raise SystemExit(
            f"topics start at day {first} but the campaign ends at day {current.total_days}; "
            "supply one topic for every new day"
        )
    now = _ts()
    plan: list[tuple[int, str]] = []
    for offset, topic in enumerate(topics):
        day = first + offset
        existing = store.get_day(campaign, day) if day <= current.total_days else None
        if existing is not None and existing.day_state is not DayState.PLANNED:
            raise SystemExit(f"day {day} is {existing.day_state.value}; only PLANNED days can change")
        plan.append((day, topic))
    for day, topic in plan:
        existing = store.get_day(campaign, day) if day <= current.total_days else None
        if existing is not None:
            store.put_day(replace(existing, topic=topic, updated_at=now))
        else:
            store.put_day(
                CampaignDay(
                    campaign_id=campaign,
                    day=day,
                    day_state=DayState.PLANNED,
                    updated_at=now,
                    topic=topic,
                    idempotency_key=derive_idempotency_key(campaign, day),
                )
            )
        print(f"extend : day {day:>2} -> {topic}")
    store.put_campaign(replace(current, total_days=new_total, updated_at=now))
    print(f"extend : campaign now {new_total} days (was {current.total_days})")


def main() -> int:
    p = argparse.ArgumentParser(description="Create + run a campaign end to end.")
    p.add_argument("--campaign", default=None)
    p.add_argument("--name", default=None)
    p.add_argument("--days", type=int, default=None)
    p.add_argument("--region", default="eu-west-1")
    p.add_argument("--email", default="", help="Notification email (required unless --skip-create).")
    p.add_argument("--timezone", default="Africa/Nairobi")
    p.add_argument("--time", dest="exec_time", default="09:00")
    p.add_argument("--skip-create", action="store_true", help="Resume: don't create/approve.")
    p.add_argument(
        "--reset",
        action="store_true",
        help="DESTRUCTIVE: delete this campaign's DynamoDB items and published "
        "S3 pages first, then create it fresh. Use after changing the model or prompt.",
    )
    p.add_argument(
        "--regenerate-from",
        type=int,
        default=0,
        metavar="N",
        help="Rewind days N..end to PLANNED (content cleared) and regenerate them with "
        "the current model and prompt. Earlier days are untouched.",
    )
    p.add_argument(
        "--through",
        type=int,
        default=0,
        metavar="N",
        help="Only generate up to day N this run (default: all days). Rewound days "
        "after N stay PLANNED and the daily schedule generates one each morning.",
    )
    p.add_argument(
        "--extend-to",
        type=int,
        default=0,
        metavar="N",
        help="Lengthen the campaign to N days. Use --topic once per day for the LAST "
        "days (ending at N); days that already exist must still be PLANNED and just "
        "get their topic replaced, new days are appended. Does not generate anything.",
    )
    p.add_argument(
        "--topic",
        action="append",
        default=[],
        help="Topic for one extended day (repeat; in day order, ending at --extend-to).",
    )
    p.add_argument(
        "--news",
        action="store_true",
        help=f"Use the news campaign: id {NEWS_CAMPAIGN}, {NEWS_DAYS} days, content_style "
        "news, placeholder topics. Creates and approves it; generates only with --through N.",
    )
    p.add_argument(
        "--roundup",
        action="store_true",
        help="Run the weekly roundup once (invokes the Lambda with the weekly_roundup event).",
    )
    p.add_argument(
        "--week",
        type=int,
        default=None,
        metavar="N",
        help="With --roundup: the week number (default: the week of today).",
    )
    args = p.parse_args()
    if args.news or args.roundup:
        args.campaign = args.campaign or NEWS_CAMPAIGN
    if args.news:
        args.name = args.name or NEWS_NAME
        args.days = args.days or NEWS_DAYS
    args.campaign = args.campaign or "aws-learning-21"
    args.name = args.name or "21 Days of AWS: What I Learned Building with the Cloud"
    args.days = args.days or 21
    if args.week is not None and not args.roundup:
        p.error("--week only applies with --roundup")
    if args.week is not None and args.week < 1:
        p.error("--week must be a positive integer")
    if args.roundup and (args.news or args.reset or args.regenerate_from or args.extend_to):
        p.error("--roundup cannot be combined with other actions")
    if args.regenerate_from or args.extend_to:
        args.skip_create = True
    if args.extend_to and not args.topic:
        p.error("--extend-to needs at least one --topic")
    through = args.through or (0 if args.news else args.days)

    function = f"aws-builder-{args.campaign}-orchestrator"
    table = f"aws-builder-{args.campaign}-campaign"
    lam = boto3.client("lambda", region_name=args.region)
    ddb = boto3.client("dynamodb", region_name=args.region)
    account = boto3.client("sts").get_caller_identity()["Account"]
    print(f"Account: {account}  region: {args.region}")
    if args.roundup:
        print("roundup:", _invoke(lam, function, _roundup_event(args.campaign, args.week)))
        return 0

    if args.reset:
        if args.skip_create:
            p.error("--reset cannot be combined with --skip-create")
        _reset(ddb, boto3.client("s3", region_name=args.region), table, args.campaign,
               f"aws-builder-{args.campaign}-articles-{account}")

    if args.regenerate_from:
        _rewind(ddb, table, args.campaign, args.regenerate_from, args.days)

    if args.extend_to:
        _extend(ddb, table, args.campaign, args.extend_to, args.topic)
        if not args.through:
            return 0

    if not args.skip_create:
        if not args.email:
            p.error("--email is required when creating a campaign")
        topics = _news_topics(args.days) if args.news else TopicPlanner().propose(args.days)
        create = {
            "authenticated": True,
            "action": "create",
            "nonce": f"bootstrap-create-{int(time.time())}",
            "timestamp": _ts(),
            "payload": {
                "campaign_id": args.campaign,
                "name": args.name,
                "total_days": args.days,
                "start_date": datetime.date.today().isoformat(),
                "execution_time": args.exec_time,
                "timezone": args.timezone,
                "topics": topics,
                "notification_email": args.email,
                **({"content_style": "news"} if args.news else {}),
            },
        }
        print("create :", _invoke(lam, function, create))
        approve = {
            "authenticated": True,
            "action": "approve",
            "campaign_id": args.campaign,
            "nonce": f"bootstrap-approve-{int(time.time())}",
            "timestamp": _ts(),
        }
        print("approve:", _invoke(lam, function, approve))

    if through == 0:
        print("Created. No days generated: the daily schedule generates one each morning.")
        return 0
    sched = {"source": "eventbridge-scheduler", "campaign_id": args.campaign}
    for day in range(1, through + 1):
        state = _day_item(ddb, table, args.campaign, day).get("day_state", {}).get("S")
        if state == "PUBLISHED":
            print(f"day {day:>2}: already PUBLISHED, skipping")
            continue
        out = _invoke(lam, function, sched)
        item = _day_item(ddb, table, args.campaign, day)
        state = item.get("day_state", {}).get("S")
        title = item.get("title", {}).get("S", "")[:60]
        print(f"day {day:>2}: {state}  {title!r}")
        if state != "PUBLISHED":
            print("Stopping: day did not publish. Lambda response:", out)
            return 1
        time.sleep(1)

    print("All days published.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
