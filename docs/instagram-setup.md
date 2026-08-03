# Preparing Instagram for the agent

Everything here is done by you, in a browser you're logged into. There's no way
to automate it: publishing requires a Meta app that's bound to your identity,
and handing that setup to a tool means handing over credentials you shouldn't
share.

Work top to bottom. Each step produces something the next one needs.

Meta renames things in this UI often. If a label below doesn't match what you
see, the *shape* of the step is still right — look for the nearest equivalent.

---

## 1. Make the Instagram account Professional

In the **Instagram mobile app** (this cannot be done on the web):

**Settings and privacy → Account type and tools → Switch to professional account**

Choose **Business**. Creator also works for publishing; Business is the safer
default because some tooling assumes it.

> Why it matters: a Personal account authenticates through the API perfectly
> well and then cannot publish. It also can't be read by peer analysis. This is
> the single most common cause of a setup that looks fine and isn't.

## 2. Link it to a Facebook Page

Instagram will offer this during the switch above. If you skipped it:

**Settings and privacy → Business tools and controls → Connect a Facebook Page**

Create an empty Page if you don't have one — nothing gets posted to it. Meta
requires the link as the permission anchor for API access.

## 3. Create a Meta app

At **developers.facebook.com → My Apps → Create App**.

- App type: **Business**
- Add the **Instagram** product (older UI calls it *Instagram Graph API*)

Note the **App ID** and **App Secret** from Settings → Basic. You need both in
step 5. The secret is a credential — treat it like a password.

## 4. Get your Instagram user ID

**Tools → Graph API Explorer.** Select your app, then run these two calls:

```
GET /me/accounts
```

Find your Page in the response and copy its `id`. Then:

```
GET /{page-id}?fields=instagram_business_account
```

The `instagram_business_account.id` in the response is your **`IG_USER_ID`** —
a long number starting `17841…`.

> It is *not* the Page ID, and not your @handle. Using the Page ID here is the
> most common reason `lumina verify` fails on the account check.

## 5. Get a long-lived access token

Still in the Graph API Explorer, add these permissions:

| Permission | Needed for |
| --- | --- |
| `instagram_basic` | Reading your account |
| `instagram_content_publish` | Posting |
| `pages_show_list` | Finding the linked Page |
| `pages_read_engagement` | Peer analysis (Business Discovery) |

Click **Generate Access Token** and approve the prompts.

**That token expires in about an hour.** Exchange it for a 60-day one — paste
this in a browser tab with your own values substituted:

```
https://graph.facebook.com/v21.0/oauth/access_token
  ?grant_type=fb_exchange_token
  &client_id=YOUR_APP_ID
  &client_secret=YOUR_APP_SECRET
  &fb_exchange_token=YOUR_SHORT_LIVED_TOKEN
```

The `access_token` in the response is your **`IG_ACCESS_TOKEN`**.

> Diarise the 60 days. When it expires, posting stops with an auth error and
> nothing tells you in advance.

## 6. Host your media somewhere public

The Graph API **does not accept file uploads.** It fetches your image or video
itself, over HTTPS, from a URL you provide. So `assets/library/` has to be
mirrored somewhere public:

- an S3 bucket or Cloudflare R2 with public read
- GitHub Pages
- any static host or CDN

The requirement is only that `MEDIA_BASE_URL/<filename>` returns the actual
file, unauthenticated, over HTTPS, with an `image/*` or `video/*` content type.

> A private bucket typically returns HTTP 200 with an HTML error page. That
> looks fine to a naive check and is useless to Instagram — `lumina verify`
> catches this case specifically.

## 7. Fill in `.env` and verify

```
ANTHROPIC_API_KEY=sk-ant-...      # console.anthropic.com → Settings → API Keys
IG_USER_ID=17841...               # step 4
IG_ACCESS_TOKEN=EAAG...           # step 5
MEDIA_BASE_URL=https://...        # step 6, no trailing slash
```

`.env` is gitignored. Never commit it, and don't paste tokens into a chat.

```bash
lumina verify
```

This makes the first real API call and tells you which of the six steps above
didn't take. Access tokens are redacted from its output, so the result is safe
to share if you need help reading it.

---

## When something fails

| `verify` says | Almost always means |
| --- | --- |
| account check failed | `IG_USER_ID` is the Page ID, or the token expired (step 4 / 5) |
| account type is `PERSONAL` | Step 1 didn't take — check in the mobile app |
| media hosting: HTTP 403/404 | Bucket isn't public, or filenames don't match `assets/library/` |
| media hosting: serves `text/html` | Private bucket returning an error page as if it were the file |
| peer lookup fails for one handle | That account is Personal — Business Discovery can't see it |
