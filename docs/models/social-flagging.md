# Social media content flags: how they work and how accurate they are

## Method
A transparent word-and-phrase list (English, Hindi in Devanagari and Hinglish), about 95 rules in seven categories. There is no machine-learning model and nothing is downloaded. Each rule has a weight. Rules that match the same text combine (overlapping matches count once). Signs of harmless context lower the score: sport or gaming talk, news reporting, quotation marks, song lyrics or fiction, awareness or fact-check wording, joke markers and negation. A category is flagged at a score of 0.5 or more. Every flag records the matched phrase, the rule id, the reasons for any lowering and a caveat. The full list is readable at `GET /social/lexicon` and in the Analysis, Content flags tab.

The flagger makes no statement about a writer's guilt, intent or mood. It says only that the wording resembles a category and a person should read it.

## Test set
`ai_engine/social/eval_corpus.json`: 142 posts written by the developers, all invented. 81 are harmless posts written to look risky on the surface (cricket banter, news, jokes, ordinary bank or lottery talk, awareness posts). The set is split:

- **dev (92 posts)**: used while the word list was written. These numbers are *tuned* and flatter the system.
- **heldout (50 posts)**: written after the list was frozen and not used for tuning. This is the more honest number. Two small later changes (removing one over-broad drug word and one word from the awareness dampener) were made because of dev errors; the heldout result did not change.

Both were written by the same people who wrote the rules, so even the heldout set is **easier than real posts**. Nothing here has been measured on real social-media data.

## Results (micro-averaged over the seven categories)
| Split | Posts | Precision | Recall | F1 | False-alarm rate on harmless posts |
|---|---|---|---|---|---|
| dev | 92 | 0.95 | 1.00 | 0.97 | 3.6% (2 of 55) |
| heldout | 50 | 1.00 | 0.71 | 0.83 | 0% (0 of 26) |
| all | 142 | 0.97 | 0.89 | 0.92 | 2.5% |

Precision: of the posts flagged, the share that deserved it. Recall: of the posts that deserved a flag, the share that got one.

## Known misses (heldout)
Seven of 24 labelled posts were missed: three sextortion-style demands worded in ways the list does not contain (including one Hinglish), two work-from-home or refund scams, one sale of a firearm with unusual wording, one chain-message forward. Recall for extortion on the heldout set was 0 of 3. Expect similar gaps on real data: the list is only as good as the phrasings its authors thought of.

## Known false alarms (dev)
"Weed killer available at the nursery" (drugs) and "This exam is killing me, I want to die (laughing emoji)" (self-harm). The second is the reason self-harm flags are labelled *welfare concern* and carry the caveat that jokes are common.

## Tests
`backend/tests/test_feat_social.py` fails if dev precision or recall drop below 0.90, heldout precision below 0.90, heldout recall below 0.60, harmless-post false alarms above 5%, or overall F1 below 0.85. The floors sit under the measured values so a small rewording does not break the build but a real regression does. Re-run the numbers with `python -m ai_engine.social.evaluate`.

## Before real use
Measure on a sample of real, lawfully collected posts labelled by officers, extend the list from what it misses, and keep a fresh unseen set for each round. Review the flags' *false positive* decisions recorded in the tool; they are the best source of new dampeners.
