#!/usr/bin/env python3
"""Fresh end-to-end test: create campaign, approve, run days 1 and 2 to S3.

Run in a terminal where AWS calls work:
    AWS_PROFILE=K1-Kitstek-Billy python tools/fresh_test_days_1_2.py

Steps:
  1. Create campaign 'aws-learning-21' (DRAFT) with the 21 planned topics.
  2. Approve it (DRAFT -> APPROVED -> READY).
  3. Invoke the orchestrator Lambda twice with a scheduler event, publishing
     day 1 then day 2 to S3/CloudFront (publisher = s3). Tags are chosen by
     Bedrock from the selectable set as part of generation/validation? No -
     the engine stores the article's generated tags; the Builder Center tag
     selection (selectable set) is a separate publish-time concern. This test
     verifies the S3 article pipeline end to end.
  4. Print the resulting day states and CloudFront URLs.

The Builder Center draft posting is done separately with the picker tools.
"""
from __future__ import annotations

import json
import sys
import time

import boto3

REGION = "eu-west-1"
TABLE = "aws-builder-aws-learning-21-campaign"
CAMPAIGN = "aws-learning-21"
FUNCTION = "aws-builder-aws-learning-21-orchestrator"

from app.topics.planner import TopicPlanner

lam = boto3.client("lambda", region_name=REGION)
ddb = boto3.client("dynamodb", region_name=REGION)


def _invoke_command(payload: dict) -> dict:
    resp = lam.invoke(
        FunctionName=FUNCTION,
        Payload=json.dumps(payload).encode("utf-8"),
    )
    body = resp["Payload"].read().decode("utf-8")
    try:
        return json.loads(body)
    except Exception:
        return {"raw": body}


def _ts() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def main() -> int:
    # Topics from the planner.
    topics = TopicPlanner().propose(21)
    print(f"Planner produced {len(topics)} topics.")

    # 1. Create (via Command API event, authenticated + explicit id).
    create = {
        "authenticated": True,
        "action": "create",
        "nonce": f"fresh-create-{int(time.time())}",
        "timestamp": _ts(),
        "payload": {
            "campaign_id": CAMPAIGN,
            "name": "21 Days of AWS: What I Learned Building with the Cloud",
            "total_days": 21,
            "start_date": "2026-09-09",
            "execution_time": "09:00",
            "timezone": "Africa/Nairobi",
            "topics": topics,
            "notification_email": "ralphgibendi01@gmail.com",
        },
    }
    print("create:", _invoke_command(create))

    # 2. Approve.
    approve = {
        "authenticated": True,
        "action": "approve",
        "campaign_id": CAMPAIGN,
        "nonce": f"fresh-approve-{int(time.time())}",
        "timestamp": _ts(),
    }
    print("approve:", _invoke_command(approve))

    # 3. Run day 1 then day 2 via scheduler events.
    sched = {"source": "eventbridge-scheduler", "campaign_id": CAMPAIGN}
    for n in (1, 2):
        out = _invoke_command(sched)
        print(f"run #{n}:", out)
        time.sleep(1)

    # 4. Report state + URLs.
    print("\n=== day states ===")
    for d in (1, 2):
        it = ddb.get_item(
            TableName=TABLE,
            Key={"pk": {"S": f"CAMPAIGN#{CAMPAIGN}"}, "sk": {"S": f"DAY#{d:04d}"}},
        ).get("Item", {})
        print(
            f"  day {d}: state={it.get('day_state',{}).get('S')} "
            f"url={it.get('publication_url',{}).get('S')} "
            f"title={it.get('title',{}).get('S','')[:50]!r}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
