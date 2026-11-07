#!/usr/bin/env python3
"""Bootstrap a campaign on whichever AWS account the active profile points at.

Creates the campaign, approves it, then invokes the orchestrator once per day so
every article is generated, validated, and published to S3/CloudFront. Safe to
re-run: use --skip-create to resume, and days already PUBLISHED are skipped.

    AWS_PROFILE=default python tools/bootstrap_campaign.py --email you@example.com
    AWS_PROFILE=default python tools/bootstrap_campaign.py --skip-create --days 21

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


def main() -> int:
    p = argparse.ArgumentParser(description="Create + run a campaign end to end.")
    p.add_argument("--campaign", default="aws-learning-21")
    p.add_argument("--name", default="21 Days of AWS: What I Learned Building with the Cloud")
    p.add_argument("--days", type=int, default=21)
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
    args = p.parse_args()
    if args.regenerate_from:
        args.skip_create = True
    through = args.through or args.days

    function = f"aws-builder-{args.campaign}-orchestrator"
    table = f"aws-builder-{args.campaign}-campaign"
    lam = boto3.client("lambda", region_name=args.region)
    ddb = boto3.client("dynamodb", region_name=args.region)
    account = boto3.client("sts").get_caller_identity()["Account"]
    print(f"Account: {account}  region: {args.region}")

    if args.reset:
        if args.skip_create:
            p.error("--reset cannot be combined with --skip-create")
        _reset(ddb, boto3.client("s3", region_name=args.region), table, args.campaign,
               f"aws-builder-{args.campaign}-articles-{account}")

    if args.regenerate_from:
        _rewind(ddb, table, args.campaign, args.regenerate_from, args.days)

    if not args.skip_create:
        if not args.email:
            p.error("--email is required when creating a campaign")
        topics = TopicPlanner().propose(args.days)
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
