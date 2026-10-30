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
