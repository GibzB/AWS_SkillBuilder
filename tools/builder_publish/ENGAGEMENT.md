# Daily badge and engagement workflow

Tracks every Builder Center badge, tells you each day what is open, and reminds
you in the evening. **You do every like, comment, reply and vote.** These tools
read and record. They never perform engagement, and a test
(`tests/unit/test_engagement_safety.py`) fails if the browser tools ever could.
They also do not coordinate likes or votes with other people: the impact badges
(Conversation Starter, Meaningful Contributor, Idea Influencer, Valued Creator)
depend on real readers and are tracked but never driven.

All commands run from the repo root with the virtual environment active:

```bash
cd "/Users/bizee/Documents/Code_Projects/AWS SkillBuilder" && source .venv/bin/activate
```

## Every morning (one command)

```bash
python tools/builder_publish/engage.py daily
```

One browser window opens with your saved login. Sign in if asked, then press
Enter. It then:

1. reads the progress of every badge you have not earned yet (once per day),
2. rewrites `docs/badge-dashboard.md`,
3. writes a reading list to `~/.aws-builder-publish/engagement/reading_list.md`,
4. prints today's status: what is open, which streaks are at risk, the focus.

If Builder Center throttles profile lookups, the routine stops, saves nothing
partial, and tells you to wait about 30 minutes. Run it again later; `--force`
repeats a snapshot you already took today.

## During the day

Read a few items from the reading list. Like what helped you, and leave one
comment that says something specific (what you learned or would add). Reply to
anyone who answers you. Then record it:

```bash
python tools/builder_publish/engage.py --done like comment reply
python tools/builder_publish/engage.py            # what is still open
```

Actions: `visit`, `like`, `comment`, `reply`, `vote`, `publish`. The snapshot
records `visit` itself, and publishing through the checklist tracker records
`publish`. Once a week, vote on one wish (`--done vote`).

## Evening reminder (macOS notification)

```bash
python tools/builder_publish/engage.py reminders install --at 20:00 --dry-run   # preview
python tools/builder_publish/engage.py reminders install --at 20:00             # install
python tools/builder_publish/engage.py reminders test                           # try it
python tools/builder_publish/engage.py reminders uninstall                      # remove
```

The agent runs a check at that time and notifies you only if a like or comment
is still unlogged. A day you have finished stays quiet.

## Day boundary

Builder Center does not say whether a "day" follows your local time or UTC.
Nairobi is UTC+3, so an action just after midnight could count for the wrong
day. Do your daily actions and log them **before 21:00 local time** and the day
counts either way.

## Where things live

| What | Where | In git? |
|---|---|---|
| Badge dashboard (progress numbers only) | `docs/badge-dashboard.md` | yes |
| Snapshot history, daily log, reading list | `~/.aws-builder-publish/engagement/` | no, it is outside the repo |
| Browser login | `~/.aws-builder-publish/chrome-profile` | no |

## The pieces

| File | Role |
|---|---|
| `badge_progress.py` | Parses progress, maps badges to actions, estimates dates, finds resets (pure) |
| `badge_snapshot.py` | Daily read-only snapshot of badge progress |
| `engage.py` | Daily log and status; entry point for `daily` and `reminders` |
| `engage_reminders.py` | macOS notification and launchd agent |
| `reading_list.py` | Read-only reading list from the home feed |
| `inspect_engagement_pages.py` | One-off capture of the popup and feed markup, to keep the readers accurate |

The 09:00 email also carries a one-line reminder of the habit. It is the same
text every day because the email cannot see your local log.
