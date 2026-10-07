# Social media intelligence

This page is for investigating officers. It explains what the Social media page does, what it does not do, and how to read what it shows you.

## What it is
A workbench for **public material you have already collected lawfully**: posts, profile pages, chat exports. You bring the material in, the system reads out the useful details (phone numbers, UPI IDs, links, places, hashtags), shows who is connected to whom and when they were active, and gives you a short list of posts whose wording deserves a human look.

## What it is not
- **It never goes to a platform.** There is no scraping, no login to any account, no call to any social-media service and no download of anything. If you do not paste, upload or type it in, it is not there.
- **It does not decide anything.** It does not say who is guilty, what someone intended or how they felt. It points to wording and patterns. You decide what they mean.
- **It does not identify people.** Two accounts sharing a phone number is a lead to check, not proof they are the same person.

## Lawful use
1. Only import material you are authorised to hold for this case (public pages you viewed, a court-ordered production, a voluntary handover).
2. Every import asks for **where it came from** and the **legal basis** (for example a notice number or order reference). Both are stored with the import and are shown in reports. You cannot import without them.
3. The system stores a **SHA-256 fingerprint** of exactly what you imported, so it can later be shown the material was not altered after import.
4. Imported material belongs to one case and is invisible from other cases. If a case is purged under the retention rules, its social material goes with it.
5. Personal details (phones, e-mails, UPI IDs, wallet addresses, message text) are hidden from roles that do not have sensitive-data access (auditors).

## Who can do what
| Action | Supervisor / Admin | Investigator | Auditor |
|---|---|---|---|
| Import, load the sample, accept a suggested link | Yes | No | No |
| See posts, accounts, run the analyses | Yes | Yes | Masked view only, no analyses |
| Review a flag (relevant / false positive / reviewed) | Yes | Yes | No |
| Export findings | Yes | Yes | No |

## The tabs
- **Overview**: counts, most active accounts, flags by category, places and hashtags. If nothing is imported yet you will see *Import material* and *Try a sample*. The sample is invented (synthetic) data so you can practise without touching a real case.
- **Priority queue**: where to start reading. Accounts and posts are ordered by a short list of visible rules, and **every line shows the points it added** (unreviewed risk wording, coordinated posting, watch-list hits, identifiers shared with other accounts, bursts of activity). It is a reading order, not a verdict. The rule weights are listed under *What am I looking at?*.
- **Accounts**: every account with its identifiers. Open one for its profile text, posts and a printable **profile sheet** (identifiers, activity by hour, linked accounts, priority reasons, watch-list matches, your team's notes).
- **Posts**: search by word, account, hashtag or category. Under each post: **English view** (machine translation of Hindi, Hinglish and other text, shown next to the original), **Watch this** (add its wording to the watch list) and **Note** (attach an analyst note).
- **Analysis**: eight views, each with a one-line explanation:
  - *Activity*: when the accounts posted, as a weekday-by-hour grid. India time (IST) by default; change the zone if the accounts are abroad.
  - *Interactions*: who mentions, replies to or reposts whom. Groups are shown in different colours. Dashed circles are accounts mentioned but not imported.
  - *Coordinated activity*: near-identical text from **different** accounts within a short time, and links or hashtags pushed by several accounts at once.
  - *Content flags*: the shortlist described below.
  - *Cross-account hints*: shared phone, e-mail, UPI ID, wallet, link or repeated profile wording.
  - *Look-alikes*: accounts whose handles or names are almost the same (`ravi_k` / `ravi.k_`, look-alike letters), which can point to one person running several accounts or to impersonation. Each pair says how strong the match is and what else supports it.
  - *Spread*: how a phrase, link or hashtag travelled: which accounts pushed it, in what order, and whether it suddenly spiked.
  - *Places map*: places named in the material, plotted on a map.
- **Watch list**: words, hashtags, handles, phone numbers or links you want to be told about. Add them here (or from a post); matches appear in the priority queue and the profile sheet. Every watch term can be switched off or deleted. Terms belong to one case.
- **Monitor**: follow one account. Enter its handle (accounts already in the case are suggested), choose where to look, write the legal basis, and press **Check now / start monitoring**. The server fetches that account's posts through the platform's official interface and adds only new ones to the case; they are read, de-duplicated and flagged like any import. Choose *Check once* or a schedule from every 15 minutes to every day. *Monitored accounts* lists each one with its last check and result, and lets you check now, pause or stop.
  - Where it can look (accounts only): **X**, **YouTube** and **Reddit** through their official APIs (each needs a key the administrator sets in `.env`, see below); the **Mastodon** public API (no key); and an **offline demonstration** source with synthetic posts for practice. A provider without its key is greyed out.
  - It never scrapes, never logs in as anyone and never uses fake accounts. Private accounts and deleted posts are out of reach, and a quiet check proves nothing. Every check and every monitoring setup is in the audit trail. Only supervisors and administrators can start monitoring.
- **Import**: paste text, upload a JSON or CSV file, or type a record by hand. Then the import history with its fingerprints.

Every panel and pop-up has a **Close** button, closes with **Esc** and closes when you click outside it.

## Suggested links to your case
The system compares what it found in the posts with the people, phones and accounts already in the case. A phone number in a profile that equals a phone entity in the case is a *strong* suggestion. A similar name is only *weak*. Nothing changes until a supervisor presses **Accept**. Accepting creates a *candidate* record marked **unverified**, pointing back to the post it came from. It does not appear in the verified graph until a reviewer verifies it in the usual way.

## How to read the flags
Each flag shows the **exact words that matched**, the **category**, **why that wording might matter** and **why it might be wrong**. The categories are: threats or violence, extortion or sextortion, financial-fraud solicitation, hate or incitement, drugs or arms trade, self-harm (welfare concern), and panic or misinformation-style forwards.

Keep in mind:
- A flag means *a person should read this*. It is not an accusation and not a risk score for the person who wrote it.
- Sarcasm, jokes, song lyrics, news reports, sport or gaming talk, quotations and translations can all use the same words. The system lowers the score when it sees such signs and tells you so, but it will still get things wrong.
- Wording that is not in the word list is not flagged. **A post with no flag is not a safe post.**
- Use the three buttons to record your own decision: *Relevant*, *False positive* or *Reviewed*, plus a short note. The history of who decided what is kept.

## How accurate is it
The flag list was checked on a small set of invented posts, including many harmless ones. The exact numbers, which posts it gets wrong and the limits of the test are in `docs/models/social-flagging.md`. In short: it catches most of what its word list was written for, it misses phrasings nobody thought of, and the numbers on real posts will be lower.

## Limits
- Links are not opened. Shortened links (bit.ly and similar) are marked; their real destination cannot be seen offline.
- Place recognition uses a built-in list of Indian places. Small localities may not be found.
- Language detection is approximate for short texts; Hinglish (Hindi in English letters) is recognised but not always.
- Similar text detection compares wording only. Real campaigns, news outlets and fan groups also copy text.
- Timestamps without a zone are assumed to be in the zone you choose at import (India by default).

## Setting up collection (administrator)
Add the keys you are entitled to hold to `.env` and restart: `X_BEARER_TOKEN`, `YOUTUBE_API_KEY`, `REDDIT_CLIENT_ID` and `REDDIT_CLIENT_SECRET`; optionally `MASTODON_INSTANCE` / `MASTODON_TOKEN`. Platform terms, rate limits and prices are set by the platforms and can change; the software reports a clear message when a key is refused or a limit is hit. The server needs internet access for these providers (the offline demonstration source needs none). `SOCIAL_AUTOCOLLECT=false` turns off background repeat searches; `SOCIAL_COLLECT_MIN_MINUTES` sets the fastest allowed repeat.
