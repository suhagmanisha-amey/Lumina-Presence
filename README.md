# Lumina Presence

An agent that regularly finds content worth posting, drafts Instagram posts from
it, and publishes the ones you approve.

It runs in **draft-and-approve** mode: the agent researches, writes, and stages
posts, and a human decides what goes live. Nothing reaches Instagram without an
explicit approval step.

```
trending topics ─┐
                 ├─→ caption writer ─→ review queue ─→ [you approve] ─→ Instagram
your asset library ┘
```

---

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

cp .env.example .env          # add your ANTHROPIC_API_KEY
$EDITOR config/brand.yaml     # fill in every FILL_ME

lumina doctor                 # tells you what's still missing
```

`lumina doctor` is the gate. It won't say *Ready* until the brand profile is
filled in and the API key is set.

### Two things worth doing properly

**The brand profile.** `config/brand.yaml` is the agent's entire understanding
of you — what you do, who for, how you sound, what you'd never say. Captions are
only as good as this file. The `voice.examples` field, where you paste two or
three of your own past captions, moves quality more than any instruction you
could write.

**Sidecar descriptions.** Drop media into `assets/library/`. The caption writer
cannot see your images, only what you tell it about them, so add a sidecar with
the matching filename:

```
assets/library/diwali-popup.jpg
assets/library/diwali-popup.txt     "the popup on Residency Road, evening light"
```

Or `.json`, for tags too:

```json
{ "caption_hint": "the popup on Residency Road", "tags": ["diwali", "retail"] }
```

Assets without a sidecar still work; their captions are noticeably vaguer.

---

## Using it

```bash
lumina verify          # once Meta is connected: prove the credentials work
lumina discover        # research + draft (this is the agent doing its job)
lumina review          # opens a review page; also lists the queue in-terminal
lumina approve <id>    # or: lumina reject <id> --note "off-brand"
lumina publish --all   # posts everything approved
lumina status          # counts by stage
```

### Learning from peers in your niche

```bash
lumina peers                    # read peer accounts, derive content angles
lumina discover --from-peers    # draft from those angles instead of web research
```

List handles under `content.peers` in `config/brand.yaml`. This uses Instagram's
Business Discovery API — public profile data only (follower count, and the
captions and like/comment counts of recent posts), and it only sees **Business
and Creator** accounts. Personal accounts will fail the lookup; that's Meta's
boundary, not a bug.

Two things worth knowing:

- **Pick peers at your scale.** A 2M-follower account's engagement patterns tell
  you very little about what works at your size. Accounts your audience *also*
  follows are the useful ones.
- **It proposes angles, not captions.** The analysis is explicitly instructed to
  produce transferable observations ("process explainers are outperforming
  product shots ~3x") rather than reworded versions of a peer's post. Near-copies
  read as derivative to the audience that follows both accounts, and the captions
  aren't yours to reuse.

Drafts live as JSON in `content/queue/`. Edit them by hand before approving —
that's the intended workflow, not a workaround. A caption you rewrote yourself
is still a caption the agent saved you from writing from scratch.

`lumina discover` also writes `content/last-research.md` — the raw research
notes behind that run. When a draft looks off, this is usually where it went
wrong, and it's otherwise invisible.

### Running it on a schedule

`.github/workflows/discover.yml` drafts on Mon/Wed/Fri and opens a pull request
containing the new drafts. Add `ANTHROPIC_API_KEY` to the repository secrets to
enable it.

**The scheduled job only ever drafts.** Publishing is a local, deliberate
command. If you later want CI to publish too, that's a separate workflow and a
decision worth making on purpose.

---

## Turning on real publishing

Until Instagram credentials are set, `lumina publish` runs a **dry run**: it
prints exactly what would be posted and changes nothing. The whole pipeline is
usable in this state, which is the point — you can judge the output quality
before doing any Meta setup.

Real publishing needs four things, and the first three are Meta's requirements,
not this project's:

1. An Instagram account converted to **Business** or **Creator** (Settings →
   Account type).
2. That account **linked to a Facebook Page** (Meta requires the Page even
   though nothing gets posted to it).
3. A **Meta developer app** with Instagram Graph API access, from which you
   generate a long-lived access token and find your Instagram user ID.
   Start at <https://developers.facebook.com/>. Long-lived tokens last ~60 days
   and need refreshing — budget for that.
4. **A public HTTPS URL for your media.** This one surprises people: the Graph
   API does not accept file uploads. It fetches your image or video itself from
   a URL, so `assets/library/` has to be mirrored somewhere publicly reachable —
   an S3 bucket, a CDN, GitHub Pages, anything that serves the same filenames.

Then fill in `.env`:

```
IG_USER_ID=17841400000000000
IG_ACCESS_TOKEN=EAAG...
MEDIA_BASE_URL=https://cdn.thelumina.in/ig
```

Then confirm it actually works — **before** a real post depends on it:

```bash
lumina verify
```

`doctor` only checks the values exist. `verify` calls Meta and proves the token
authenticates, the account is a Business/Creator account that can publish, and
your media URL is publicly fetchable and serves an actual image. Every one of
those failures would otherwise surface mid-publish, after a media container had
already been created.

```
  ✓ credentials    all four values present
  ✓ account        @luminapresence (id 17841400000000000)
  ✓ account type   BUSINESS
  ✓ meta quota     3/50 posts used in last 24h
  ✓ media hosting  https://cdn.thelumina.in/ig/shot.jpg → image/jpeg
```

Set any one credential and not the others and it stays in dry-run rather than
half-working. Access tokens are redacted from all error output, so `verify`
results are safe to paste into an issue.

Once live, `lumina publish` asks for confirmation before posting and refuses to
exceed `safety.max_posts_per_day`. Both are deliberate friction on an action
that is public and hard to undo.

---

## How it's put together

| Path | What it does |
| --- | --- |
| `config/brand.yaml` | Everything the agent knows about you |
| `src/lumina/sources/trends.py` | Live web research → topics (two passes: search, then structure) |
| `src/lumina/sources/assets.py` | Reads your media library and its sidecars |
| `src/lumina/compose.py` | Pairs a topic with an asset, writes the caption |
| `src/lumina/review.py` | Renders the review page |
| `src/lumina/publish/` | Pluggable publishers: dry-run (default) and Graph API |
| `src/lumina/store.py` | Drafts as JSON on disk, one directory per stage |

Drafts are files rather than database rows so you can read them, hand-edit them,
and see them in a `git diff` before approving. For a review queue that's a
feature.

The publisher is a `Protocol` in `publish/base.py`. Posting to somewhere other
than Instagram means writing one class with `check()` and `publish()`; nothing
upstream of it needs to change.

### Guardrails

- Publishing only ever acts on **approved** drafts.
- The daily cap is enforced at publish time, whatever the caller asked for.
- Hashtags are normalised in code — pinned tags always present, banned tags
  always stripped — so a model that ignores the instruction can't override you.
- The caption writer is told, in the prompt and in `voice.never`, not to invent
  statistics, prices, dates, or quotes. Verify anything factual anyway; that
  instruction reduces the risk, it doesn't eliminate it.
- If research turns up nothing solid, `discover` says so and drafts nothing
  rather than padding the queue with filler.

```bash
pytest        # 19 tests, no network required
```
