# 21 Days of AWS — Progress Checklist

> Extended to **26 articles** so the posting streak can reach 🔥 21 (days 6–26 are
> one unbroken chain). The campaign id and title keep the original "21" name.

Campaign: `aws-learning-21` ("21 Days of AWS: What I Learned Building with the Cloud")

> 🔥 **Current posting streak: Day 20** — keep publishing consecutive days to extend the chain.
> chain at 🔥 Streak Day 1. (Days 1–5 were the previous streak, broken when
> day 6 was skipped.) Update this number each time you publish.

Three stages per day:
- **S3**: article generated, validated, and published to S3/CloudFront (automated).
- **BC draft**: filled into the Builder Center editor (title/description/body + auto-tags) via the assisted tool.
- **BC published**: you clicked Publish on Builder Center (live post).
- **Streak**: consecutive-posting counter. 🔥 N = Nth day of the current
  unbroken chain; `· broken` = part of a previous, now-ended streak.

Command to draft a day:
```
AWS_PROFILE=default python tools/builder_publish/batch_publish.py --from N --to N --auto-tags
```

| Day | Topic | S3 | BC draft | BC published | Streak | Notes |
|----:|-------|:--:|:--------:|:------------:|:------:|-------|
| 1 | AWS account fundamentals & security mindset | ✅ | ✅ | ✅ | · broken | prev streak |
| 2 | IAM users, roles, least privilege | ✅ | ✅ | ✅ | · broken | prev streak |
| 3 | Amazon S3 fundamentals | ✅ | ✅ | ✅ | · broken | prev streak |
| 4 | S3 lifecycle & storage optimization | ✅ | ✅ | ✅ | · broken | prev streak |
| 5 | Amazon EC2 fundamentals | ✅ | ✅ | ✅ | · broken | prev streak (chain ended — day 6 skipped) |
| 6 | EC2 security groups | ✅ | ✅ | ✅ | 🔥 1 | published live — fresh streak start |
| 7 | Amazon VPC fundamentals | ✅ | ✅ | ✅ | 🔥 2 | published live |
| 8 | Subnets, route tables, internet access | ✅ | ✅ | ✅ | 🔥 3 | published live |
| 9 | AWS Lambda | ✅ | ✅ | ✅ | 🔥 4 | published live |
| 10 | Amazon API Gateway | ✅ | ✅ | ✅ | 🔥 5 | published live |
| 11 | Amazon EventBridge | ✅ | ✅ | ✅ | 🔥 6 | published live — first Nova Pro article |
| 12 | Amazon CloudWatch | ✅ | ✅ | ✅ | 🔥 7 | published live |
| 13 | AWS Systems Manager | ✅ | ✅ | ✅ | 🔥 8 | published live |
| 14 | Amazon DynamoDB | ✅ | ✅ | ✅ | 🔥 9 | published live |
| 15 | Amazon RDS fundamentals | ✅ | ✅ | ✅ | 🔥 10 | published live |
| 16 | Amazon SNS | ✅ | ✅ | ✅ | 🔥 11 | published live |
| 17 | Infrastructure as Code (Terraform/CloudFormation) | ✅ | ✅ | ✅ | 🔥 12 | published live (regenerated once; first run omitted its template file) |
| 18 | AWS security fundamentals | ✅ | ✅ | ✅ | 🔥 13 | published live |
| 19 | AWS cost optimization | ✅ | ✅ | ✅ | 🔥 14 | published live (hand-edited: removed invented savings claim) |
| 20 | Serverless architecture patterns | ✅ | ✅ | ✅ | 🔥 15 | published live |
| 21 | Amazon SQS and decoupling with queues | ✅ | ✅ | ✅ | 🔥 16 | published live (regenerated after a placeholder failure; example run end to end by hand) |
| 22 | AWS Step Functions | ✅ | ✅ | ✅ | 🔥 17 | published live |
| 23 | Amazon CloudFront and content delivery | ✅ | ✅ | ✅ | 🔥 18 | published live 20 Nov (hand-rewritten; commands run in own account) |
| 24 | AWS KMS and encryption at rest | ✅ | ✅ | ✅ | 🔥 19 | published live 21 Nov; check the live post uses "KMS key" and describes envelope encryption correctly |
| 25 | Amazon Route 53 and DNS fundamentals | ✅ | ✅ | ✅ | 🔥 20 | published live 22 Nov (rewritten by hand to fix factual errors) |
| 26 | Lessons learned: connecting the AWS fundamentals | ✅ | ⬜ | ⬜ | · broken | NEXT — generated 22 Nov; replace the body with the rewrite, publish 23 Nov for 🔥 21 |

> S3 column assumes the campaign articles are generated (they were, in the last
> full run). Verify a day's S3 page by opening
> `https://d2e14hwggytv3g.cloudfront.net/day/NNNN.html`.
> As you go: tick the BC draft / published boxes, and bump the 🔥 banner at the
> top to the streak day you just published.
>
> The 🔥 numbers for days 7–21 are PROJECTED — they assume you publish on
> consecutive days. If you skip a day, the chain breaks: mark that day
> `· broken` and restart the next published day at 🔥 1.

## Builder Center badges: live status

The earned, in-progress and not-started lists used to live here and went stale.
They are now generated from your real profile by a daily, read-only snapshot:

- **Dashboard:** [docs/badge-dashboard.md](badge-dashboard.md) (progress bars,
  estimated finish dates, what changed, today's priorities). It is created by the
  first snapshot and rewritten on every run.
- **Morning routine:** `python tools/builder_publish/engage.py daily`
  (snapshot, dashboard, reading list, status in one browser window).
- **Log what you did:** `python tools/builder_publish/engage.py --done like comment`
- **Full workflow and commands:** [tools/builder_publish/ENGAGEMENT.md](../tools/builder_publish/ENGAGEMENT.md)

### Why no automation here (honest note)

- Streaks require a real daily visit/like/comment. Faking them means evading
  controls, which risks the account and its perks.
- Impact badges depend on real people engaging with genuinely useful posts;
  there is nothing to automate that is not gaming.
- The platform's throttles are a signal to go human-paced; the daily habit both
  earns the badges and stays within the limits.
- The tools read and record only. A test fails if the browser tools ever gain a
  way to like, comment, reply or vote.

---

## Badge strategy — the 90-day plan

This turns the badge list above into a dated, sequenced plan. The spine is the
daily 6-step habit; the milestones below tell you WHEN each badge should land
if you keep the chain unbroken. Day 1 of this plan = the next day you start the
daily habit (you're already mid-series, so start now).

### Priority order (what to protect first)

1. **Never break a streak.** A missed day resets a 90-day counter to zero — the
   most expensive mistake available. The single highest-value action each day
   is the <60-second visit. Do that even on days you do nothing else.
2. **Publish weekly, not just daily.** The 4-Week Article Publishing Streak only
   needs ONE article per calendar week — the 21-day run covers this easily, but
   don't let a whole week pass with zero publishes once the series ends.
3. **Engage genuinely.** Likes + comments feed three streaks AND seed the impact
   badges. One real comment a day does double duty.
4. **Impact badges are lagging indicators.** You can't force them; they arrive
   as your back-catalog accumulates likes/replies. Keep publishing useful stuff
   and they follow.

### Timeline — when each badge should land

| Milestone | Day of habit | Badges that unlock | Depends on |
|-----------|:------------:|--------------------|------------|
| Week 1 close | Day 7 | 7-Day Like Streak, 7-Day Comment Streak | ≥1 like + ≥1 comment every day |
| Week 4 close | Day 28 | 30-Day Visit Streak, 30-Day Like Streak, 30-Day Comment Streak, 4-Week Article Publishing Streak | unbroken daily habit + weekly publish |
| Week 13 close | Day 90 | 90-Day Visit Streak, 90-Day Like Streak, 90-Day Comment Streak | unbroken daily habit for 90 days |
| Rolling | as engagement accrues | Valued Creator, Conversation Starter, Meaningful Contributor, Idea Influencer | real likes/replies/votes from others |
| Optional | weekly for 4 weeks | 4-Week Wish Vote Streak | vote on 1 wish per week |

> Note the Like/Comment streaks already show "in progress" — if those counters
> are mid-chain, protect them: a single missed day restarts them at 0.

### The daily 6-step habit (copy this, do it every day)

Tie it to the "Day N ready to post" email the automation sends you:

1. ☐ **Visit** Builder Center while signed in (the streak-saver — never skip).
2. ☐ **Publish** the day's drafted article (while the 21-day series lasts).
3. ☐ **Like** ≥1 piece of content you genuinely found useful.
4. ☐ **Comment** ≥1 genuine comment on someone else's content.
5. ☐ **Reply** to any new comments/likes on your own posts.
6. ☐ **Vote** on 1 wish (only needed ~weekly for the wish badges).

Steps 1, 3, 4 are the non-negotiable streak-feeders (<5 min total). Steps 2, 5,
6 are situational. On a zero-energy day, do at least steps 1, 3, 4.

### After the series ends (day 26)

The publishing run finishes on day 26, but the big streak badges need 90 days.
Keep going:

- **Keep visiting daily** → 90-Day Visit Streak (the headline badge).
- **Keep liking + commenting daily** → 90-Day Like/Comment Streaks.
- **Publish something weekly** (doesn't have to be daily) → keeps the publishing
  cadence alive and keeps feeding the impact badges with fresh content.
- **Run the morning routine** (`python tools/builder_publish/engage.py daily`)
  to refresh the badge dashboard and see what is open today.

### Weekly streak tracker (tick a box each day you keep the chain)

Protecting the chain matters more than any single day's content. Mark each day
you completed steps 1/3/4 (visit + like + comment).

- Week 1:  ☐ ☐ ☐ ☐ ☐ ☐ ☐  → 7-day Like + Comment streaks
- Week 2:  ☐ ☐ ☐ ☐ ☐ ☐ ☐
- Week 3:  ☐ ☐ ☐ ☐ ☐ ☐ ☐
- Week 4:  ☐ ☐ ☐ ☐ ☐ ☐ ☐  → 30-day Visit/Like/Comment + 4-Week Publishing
- Weeks 5–13: keep the chain → 90-day Visit/Like/Comment at day 90
