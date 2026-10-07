# Surveillance reports

This page is for officers running or supervising a surveillance operation. It explains how to record an operation, how the log protects itself against later changes, and how to produce the report.

## Lawful use first
- Surveillance may be carried out only under valid authority. **You cannot create an operation without entering the authority text and its reference number** (for example the order or permission number). Both are printed on every report.
- Set the authorised period (start, and end if there is one). Entries recorded outside it are **flagged** in the log and the report. Whether such entries can be used is a legal decision for you and your supervisor, not for the software.
- Record only what was observed. Keep opinions out of the observation text; put confidence in the ratings.
- Keep to your department's retention rules. The operation belongs to one case, and is removed with the case when the case is purged.

## Who can do what
| Action | Supervisor / Admin | Investigator | Auditor |
|---|---|---|---|
| Create an operation, add or amend entries, load the sample | Yes | No | No |
| Read the log, verify it, run movement analysis, generate reports | Yes | Yes | Read a masked log only |
| Close an operation | Yes | No | No |

Roles without access to sensitive data see the entries with observation text, names, registrations and coordinates hidden.

## Recording an operation
1. **New operation**: codename, subject (a person already in the case, or described in words), objective, authority, period, supervising officer, team.
2. **Add entry** for each observation: date and time (and the zone that time was in), place, observer, what was seen, whether the subject was seen, vehicles, people, evidence IDs from the evidence vault, and the two ratings.
3. Place names the system knows (Indian towns and localities) are given an approximate position for the map; latitude and longitude from the observer's device are better and are marked as recorded.

## The log cannot be edited
Entries cannot be changed or deleted. Each one is stored together with a fingerprint (SHA-256) that includes the fingerprint of the entry before it, like links in a chain. If you find a mistake, use **Amend**: you give a reason, and a **new version** is added that points back to the original. Both remain visible; the report shows the current version and lists every amendment with its reason.

**Verify now** recomputes every fingerprint. *Chain verified* means nothing was changed, removed or re-ordered since it was written. *Chain BROKEN* lists where the check fails. The fingerprints are also copied into the audit log (and the tamper-evident ledger when it is turned on), so an attempt to rewrite the whole chain quietly is also detectable.

What the check does not show: that an observation was **true**. It shows only that the record is unchanged.

## Reading the ratings
Each entry has two letters and numbers, as in *B2*.
- **Source, A to F**: how reliable the observer is. A completely reliable, B usually reliable, C fairly reliable, D not usually reliable, E unreliable, F cannot be judged.
- **Information, 1 to 6**: how well the information is confirmed. 1 confirmed by other sources, 2 probably true, 3 possibly true, 4 doubtful, 5 improbable, 6 cannot be judged.
- **F6** is the honest default when you do not know yet. The ratings are the observer's assessment. The system does not change them. Hover over a rating in the log to see its meaning.

## Movement analysis
Built only from entries that have a position. It shows:
- **Route map**: the sightings joined in time order. Dashed markers are approximate. A straight line is not the route taken.
- **Places and time spent**: visits, days seen, and the usual hours. Time spent is the gap between the first and last sighting at a place, so it is a minimum.
- **What the log shows**: short plain sentences such as "this place was visited on 3 separate days, usually between 18:00 and 19:00". They describe what was recorded. A small sample can look like a routine by chance.
- **Meetings**: two people recorded at the same place within the time window you set. This shows they were near each other, not that they know each other.
- **Vehicles**: registrations are tidied to a standard form (spaces removed, capitals). Unusual formats are marked, since they are often a typing slip.
- **Movement between sightings**: implausible speeds point to a wrong time or place in an entry.
Times when nobody was watching are gaps in coverage, not evidence that nothing happened.

## Reports
Choose **PDF** (to print and sign), **Word** (to edit), **HTML** or **JSON**. A report contains: header with authority and period; summary; the full log in time order; amendments with reasons; subjects, vehicles and places; movement findings; any social-media findings linked to the same case (when that module has them); evidence references; limitations; sign-off lines; and an integrity block with the chain head fingerprint, number of entries, who generated it and when, and the fingerprint of the report content. Each PDF page footer repeats the chain head and report fingerprint.

The downloaded file's own fingerprint is shown after download and written to the audit log, so a printed copy can later be matched against the record. Every report generation is logged.

## Closing an operation
A supervisor closes an operation with a closing note. A **closed operation accepts no further entries or amendments**. Closing is recorded with its own fingerprint and cannot be undone.

## Limits
- The chain lives in the same database as the entries. Someone with full database access could rewrite both; the audit-log copies of the fingerprints are what expose this, so keep the audit log protected and backed up.
- Times are stored in UTC and displayed in the zone you pick (India by default). Observers should record times from a synchronised clock.
- PDF reports support Latin script; Devanagari text in a PDF may not show correctly. Use the Word or HTML version for those reports.
- Place matching uses a built-in list of Indian places and will not know small localities.
