# Ingestion and translation: a guide for officers

This guide explains what happens to a FIR or complaint after you upload it, what each percentage on the screen
means, and where the system is likely to be wrong. The short version:

* The system **proposes**. You **decide**. Nothing enters the case graph until you accept it and press
  **Verify document**.
* Every proposed item shows the **words it came from** (highlighted) and a **plain-language reason**.
* The English text is a **reading aid made by an offline rule-based engine, not a neural translator**. Check names,
  places, sections and amounts against the original.

---

## 1. What happens to an uploaded document

1. **Upload.** The file is stored unchanged as evidence with a SHA-256 fingerprint. Uploading the same file to the same
   case twice does not create a second copy; you are shown the first one.
2. **Reading the text.** Typed text (`.txt`, Word) is read directly. PDFs use their text layer if present. Photos and
   scans are read with OCR (text recognition), which can misread characters.
3. **Cleaning.** Hindi and other Indian-script digits are converted to ordinary digits for matching (the original text is
   kept for display). Common OCR mix-ups are repaired only where the position makes the answer clear (for example a
   letter `O` inside a phone number becomes `0`). Every repair is flagged for you to check.
4. **Language.** The script and typical grammar words decide English, Hindi, Marathi, Bengali, Gujarati, Punjabi,
   Tamil, Telugu, Kannada, Malayalam, Urdu, or Hinglish (Hindi typed in English letters). You can override this when you
   upload.
5. **FIR fields.** FIR number, police station, district, dates and times, sections, complainant, father's name, address,
   place of occurrence, accused description, stolen property and investigating officer are read from their labels
   (in English or Hindi/Marathi).
6. **Items (entities).** Phone numbers, vehicle numbers, bank accounts, IFSC codes, UPI IDs, e-mails, Aadhaar, PAN,
   amounts, dates, sections and people are found and written in one standard form. Example: `+91-94140 55667`,
   `094140-55667` and `९४१४०५५६६७` all become `9414055667`, so the same number in two FIRs is recognised as one.
7. **Roles.** People are given a role (complainant, accused, victim, witness, relative, police officer, investigating
   officer) from cue words such as "S/o", "suspected", "eyewitness", or from the form label.
8. **Relationships.** Simple links are proposed, such as *person owns phone*, *person used vehicle*, *person called
   number*, *person transferred money to account*. Each shows the sentence that supports it.
9. **English view.** The text is translated sentence by sentence (see section 4).
10. **Summary and review.** You see one summary and a review screen. Only after you verify does anything go to the graph.

---

## 2. The summary tiles

| Tile | What it tells you |
|---|---|
| **Language** | What the system believes the document is written in, and how sure it is. "Mixed languages" means more than one was found. A low number means: set the language yourself and upload again. |
| **Text reading** | For scans and photos, how confident the OCR was. Below about 60% expect misread characters. "Typed text" means no OCR was needed. |
| **FIR fields found** | How many of the standard FIR fields were read, and which key ones are missing. A missing field is shown honestly as *not found*, never guessed. |
| **Items found** | How many entities were found and how many need your check. |
| **Relationships** | How many links were proposed and how many need your check. |
| **English view** | Which engine wrote it and what share of the words it understood. |

If something is wrong with the file you get a plain message above the tiles. The common ones:

* **Not a supported type.** Use PDF, PNG, JPG, TIFF, WEBP, Word (.docx) or plain text.
* **Empty file.** The file has no content. Check it saved or scanned properly.
* **No readable text.** Scan again: flat, well lit, straight-on, at least 300 dpi. For a PDF try the original text version.
* **Low scan quality.** Text was read but with low confidence. Check every number against the image.
* **No FIR number / not a standard FIR.** Entities were still extracted; check them carefully.
* **An Aadhaar number does not pass its check digit** or **a date is not a real calendar date.** Almost always an
  OCR or typing error; compare with the source.

---

## 3. What each confidence number means

There are four different percentages. They answer different questions.

**Item confidence ("98% sure" on an entity).** How reliable the *way it was found* is, not a statistical probability.

| Confidence | How it was found |
|---|---|
| about 97% | A pattern plus a check that must pass: Aadhaar check digit, real state code on a vehicle number, PAN/IFSC layout |
| about 95% | It stood next to a label ("Complainant:", "Mobile:") |
| about 90% | A strong pattern (10-digit mobile starting 6 to 9, an amount with ₹ or "Rs") |
| about 86% | The writer speaks in the first person ("I", "मैं") |
| about 82% | A cue word ("son of", "suspected", "eyewitness") |
| about 80% | It needed an OCR repair |
| about 62% or less | A weak cue, inferred from context, or the role could not be decided |

These numbers are set by hand from how each method behaved on a small test set. They are **not** calibrated on real
FIRs. Treat 90% and above as "usually right, still look", and anything lower as "look carefully".

**"Please check" (needs review).** Set whenever the item was repaired, has an unusual shape, is a weak guess, or
conflicts with something else. Items marked this way are never included by **Accept high-confidence items**.

**OCR confidence.** How sure the text-recognition step was about the characters. It says nothing about whether the
*content* is right.

**English view: confidence and "words understood".** For each sentence the engine reports how much of it came from
its glossary or grammar rules. **Words understood** is the share of words it recognised. A sentence marked
*Not understood* contains words the engine does not know; those words are highlighted in the text and listed above it.

---

## 4. About the English view

The Original | English tab shows each original sentence beside its English rendering.

* **Engine.** The line above the text says which engine produced it. By default it is the **offline rule-based glossary
  engine (not a neural model)**: a large police and legal glossary, Hindi and Marathi grammar rules, and transliteration
  for names and places. If your administrator has installed a local neural model it is used per sentence, and it is
  labelled as such; its output is checked to make sure phone numbers, vehicle numbers, amounts and sections were not
  changed. If that check fails, the rule engine is used instead.
* **Quality label per sentence.**
  *Glossary match*: every word was known. *Rule-based*: some words were rearranged or transliterated by rule.
  *Not understood*: unknown words remain. *Already English*: nothing to translate.
* **Highlighted words** are words the system did not understand. It shows a spelling, never a guessed meaning.
* **Never translated:** phone numbers, vehicle numbers, account numbers, IFSC, UPI, Aadhaar, PAN, e-mails, transaction
  IDs, FIR numbers, section numbers and amounts are copied exactly.
* **Names and places** are transliterated (written in English letters), not translated. A well-known city or state is
  spelled in its usual English form (for example जयपुर becomes Jaipur). For Hindi text this is exact; for other scripts
  it is matched by sound, and the item is flagged.
* **Word order.** Hindi and Marathi put the verb last; the engine reorders simple sentences, but long, joined
  sentences can read awkwardly (for example "In money account number ... deposited"). Read it as a gloss.

**Language coverage.** Hindi, Marathi and Hinglish have the fullest glossaries and grammar. Bengali, Gujarati, Punjabi,
Tamil, Telugu, Kannada, Malayalam and Urdu have a core police vocabulary and transliteration only: expect word-for-word
output with many highlighted words.

---

## 5. Reviewing a document

Open a document from the list (or straight after uploading it). The **Review** tab lists every item and relationship.

* **Accept / Reject / Edit** on each card. *Edit* lets you correct the text or the type; an edited item is saved as a new
  item with the note "edited", and keeps the original text for the audit trail.
* Each card shows *Why* (plain reason), the **evidence** with the matched words highlighted, the confidence, and any
  flags such as *ocr repaired*.
* **Filters:** by type, by decision, by "only items to check", or search.
* **Accept high-confidence items** accepts undecided items at 90% or above that are not flagged. It does not touch the
  rest. Use **Accept everything shown** only after you have looked at the list.
* A **relationship can be accepted only when both of its ends are accepted items.** If one end is rejected, the
  relationship is set back to undecided.
* Amounts, dates and sections are listed separately. They stay with the document as evidence and are not added to the
  graph as nodes.
* **Verify document** adds every accepted item and relationship to the case graph, marked as verified by you.
  You must accept at least one item. Undecided items are not added.
* **Reject document** records that this document is not to be used. Nothing is added.
* **Analyse again** reads the original file again with the current engine and clears earlier decisions.
  Only use it on documents not yet verified.

Other tabs: **FIR fields** (each field, its status and the words it came from), **Original | English**, and **Source
text**.

---

## 6. Limits you should know about

* The extractor is tuned on a **small set of invented documents** (30 FIRs and complaints). It has not been measured
  on real case files. Expect lower accuracy on unfamiliar layouts, handwriting, and heavily degraded scans.
* **Handwriting** is not supported. OCR of printed text works best; photographs at an angle or with shadows are risky.
* **Roles and links are inferred from wording.** A phone number given as "the accused called me on 98xxxxxx" is left
  unlinked because it is unclear whose it is. Pronouns ("he", "his PA") are followed only in simple cases. Always check
  the highlighted sentence.
* **Names** with the same spelling are treated as the same person only when the identifier is the same; two people with
  one name can be merged by mistake if you accept both.
* **Sections** are recognised for IPC, BNS, the IT Act, NDPS, Arms Act, POCSO, Prevention of Corruption, PMLA and Dowry
  Prohibition. The offence name shown is a short label, not legal advice. Sections of the procedure codes (CrPC/BNSS) are
  ignored on purpose.
* **Translation** is a reading aid. It can drop nuance, mis-order clauses and miss words. It is never a certified
  translation and must not be used as one.
* **Confidence values are not probabilities.** A 97% item can still be wrong (for example the correct number
  belonging to a different person).
* All of this runs **offline on the server**. Nothing in the document is sent to an outside service.

If a result looks wrong, reject or edit it. That is expected use, not a failure of the process.
