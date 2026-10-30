# 21 Days of AWS — Progress Checklist

Campaign: `aws-learning-21` ("21 Days of AWS: What I Learned Building with the Cloud")

Three stages per day:
- **S3**: article generated, validated, and published to S3/CloudFront (automated).
- **BC draft**: filled into the Builder Center editor (title/description/body + auto-tags) via the assisted tool.
- **BC published**: you clicked Publish on Builder Center (live post).

Command to draft a day:
```
AWS_PROFILE=K1-Kitstek-Billy python tools/builder_publish/batch_publish.py --from N --to N --auto-tags
```

| Day | Topic | S3 | BC draft | BC published | Notes |
|----:|-------|:--:|:--------:|:------------:|-------|
| 1 | AWS account fundamentals & security mindset | ✅ | ✅ | ✅ | published live |
| 2 | IAM users, roles, least privilege | ✅ | ✅ | ✅ | published live |
| 3 | Amazon S3 fundamentals | ✅ | ✅ | ✅ | published live |
| 4 | S3 lifecycle & storage optimization | ✅ | ✅ | ✅ | published live |
| 5 | Amazon EC2 fundamentals | ✅ | ✅ | ✅ | published live |
| 6 | EC2 security groups | ✅ | ⬜ | ⬜ | NEXT — resume here |
| 7 | Amazon VPC fundamentals | ✅ | ⬜ | ⬜ | |
| 8 | Subnets, route tables, internet access | ✅ | ⬜ | ⬜ | |
| 9 | AWS Lambda | ✅ | ⬜ | ⬜ | |
| 10 | Amazon API Gateway | ✅ | ⬜ | ⬜ | |
| 11 | Amazon EventBridge | ✅ | ⬜ | ⬜ | |
| 12 | Amazon CloudWatch | ✅ | ⬜ | ⬜ | |
| 13 | AWS Systems Manager | ✅ | ⬜ | ⬜ | |
| 14 | Amazon DynamoDB | ✅ | ⬜ | ⬜ | |
| 15 | Amazon RDS fundamentals | ✅ | ⬜ | ⬜ | |
| 16 | Amazon SNS | ✅ | ⬜ | ⬜ | |
| 17 | Infrastructure as Code (Terraform/CloudFormation) | ✅ | ⬜ | ⬜ | |
| 18 | AWS security fundamentals | ✅ | ⬜ | ⬜ | |
| 19 | AWS cost optimization | ✅ | ⬜ | ⬜ | |
| 20 | Serverless architecture patterns | ✅ | ⬜ | ⬜ | |
| 21 | Lessons learned: connecting the fundamentals | ✅ | ⬜ | ⬜ | |

> S3 column assumes the campaign articles are generated (they were, in the last
> full run). Verify a day's S3 page by opening
> `https://d1x0urkz16usf1.cloudfront.net/day/NNNN.html`.
> Update BC draft / published columns as you go.

## Builder Center badges — live status & strategy

Scraped live from `https://builder.aws.com/profile?tab=badges` via
`tools/builder_publish/harvest_badges.py` (reuses your login; reads each
badge's name, description, and state from the rendered grid). Re-run that tool
to refresh this section.

Badges reward GENUINE engagement — there is no automation shortcut, and the
platform rate-limits rapid activity (the ~1-article/hour create limit and the
GetUserProfileByProfileId throttle). The winning play is a small, consistent
DAILY habit, not a burst. The 21-day series is the engine; these habits earn
the badges around it.

As of the last scrape: **7 earned, 12 in progress, 2 not started** (21 total).

### ✅ Earned (7)

- [x] **7-Day Visit Streak** — Visit Builder Center once a day for 7 consecutive days while signed in.
- [x] **First Article** — Publish your first article.
- [x] **First Wish** — Publish your first wish in AWS Wishlist.
- [x] **Discussion Debut** — Join the conversation by leaving one comment on any content.
- [x] **Hello, World!** — Complete the About section in your profile.
- [x] **Knowledge Seeker** — Read 10 different articles.
- [x] **Photo Finisher** — Upload a photo to your profile.

### 🔄 In progress — keep the daily habit going (12)

Streaks (just keep showing up / liking / commenting daily — don't break the chain):

- [ ] **30-Day Visit Streak** — Visit at least once a day for 30 consecutive days. (7-day already done)
- [ ] **90-Day Visit Streak** — Visit at least once a day for 90 consecutive days.
- [ ] **7-Day Like Streak** — Leave ≥1 like on any content each day for 7 consecutive days.
- [ ] **30-Day Like Streak** — ...for 30 consecutive days.
- [ ] **90-Day Like Streak** — ...for 90 consecutive days.
- [ ] **7-Day Comment Streak** — Leave ≥1 comment on any content each day for 7 consecutive days.
- [ ] **30-Day Comment Streak** — ...for 30 consecutive days.
- [ ] **90-Day Comment Streak** — ...for 90 consecutive days.
- [ ] **4-Week Article Publishing Streak** — Publish an article weekly for 4 consecutive weeks.

Impact (needs real engagement from others on genuinely useful content):

- [ ] **Valued Creator** — Get 10 likes on five of your articles.
- [ ] **Conversation Starter** — Get a reply on 10 of your comments.
- [ ] **Meaningful Contributor** — Get 10 likes on five of your comments.

### ⚪ Not started (2)

- [ ] **Idea Influencer** — Get 10 votes on your wishes.
- [ ] **4-Week Wish Vote Streak** — Vote on a wish each week for 4 consecutive weeks.

### The 5-minute daily habit (this earns almost everything above)

Tie it to the "Day N ready to post" email you already receive:

1. **Visit** Builder Center → feeds the 30/90-day Visit Streaks.
2. **Publish the day's drafted article** → feeds the 4-Week Publishing Streak.
3. **Leave ≥1 like** on content you found good → feeds the 7/30/90 Like Streaks.
4. **Leave ≥1 genuine comment** → feeds the 7/30/90 Comment Streaks + Discussion.
5. **Reply** to comments/likes on your posts → feeds Conversation Starter.
6. **Vote on one wish** (occasionally) → feeds Idea Influencer + Wish Vote Streak.

Do those once a day and every in-progress streak badge lands on schedule, while
the impact badges (Valued Creator, Conversation Starter, Meaningful Contributor)
build as real people engage with useful posts. Spacing it daily also keeps you
clear of the rate limits.

### Why no automation here (honest note)

- Streaks require a real daily visit/like/comment — faking them means evading
  controls, which risks the account and its perks.
- Impact badges depend on real people engaging with genuinely useful posts;
  there's nothing to automate that isn't gaming.
- The platform's throttles are a signal to go human-paced; the daily habit both
  earns the badges and stays within the limits.

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

### After the 21-day series ends

The publishing run finishes on day 21, but the big streak badges need 90 days.
Keep going:

- **Keep visiting daily** → 90-Day Visit Streak (the headline badge).
- **Keep liking + commenting daily** → 90-Day Like/Comment Streaks.
- **Publish something weekly** (doesn't have to be daily) → keeps the publishing
  cadence alive and keeps feeding the impact badges with fresh content.
- **Re-run the harvester** every week or two to refresh this file:
  `AWS_PROFILE=K1-Kitstek-Billy python tools/builder_publish/harvest_badges.py`
  then update the ✅/🔄/⚪ lists above.

### Weekly streak tracker (tick a box each day you keep the chain)

Protecting the chain matters more than any single day's content. Mark each day
you completed steps 1/3/4 (visit + like + comment).

- Week 1:  ☐ ☐ ☐ ☐ ☐ ☐ ☐  → 7-day Like + Comment streaks
- Week 2:  ☐ ☐ ☐ ☐ ☐ ☐ ☐
- Week 3:  ☐ ☐ ☐ ☐ ☐ ☐ ☐
- Week 4:  ☐ ☐ ☐ ☐ ☐ ☐ ☐  → 30-day Visit/Like/Comment + 4-Week Publishing
- Weeks 5–13: keep the chain → 90-day Visit/Like/Comment at day 90
