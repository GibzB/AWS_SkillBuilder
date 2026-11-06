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
    args = p.parse_args()

    function = f"aws-builder-{args.campaign}-orchestrator"
    table = f"aws-builder-{args.campaign}-campaign"
    lam = boto3.client("lambda", region_name=args.region)
    ddb = boto3.client("dynamodb", region_name=args.region)
    print(f"Account: {boto3.client('sts').get_caller_identity()['Account']}  region: {args.region}")

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
    for day in range(1, args.days + 1):
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
