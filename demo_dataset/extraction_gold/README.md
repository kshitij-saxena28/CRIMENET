# Extraction gold set

Hand-written FIRs / complaints with hand-written expected output, used by `scripts/eval_extraction.py`
and `backend/tests/test_extraction_quality.py`. All people, numbers and identifiers are invented
(Aadhaar numbers pass the Verhoeff check but are not real). Nothing here comes from a real case.

* `g*.gold` = **dev** split (used while building the extractor).
* `h*.gold` = **heldout** split, written before the new extractor and never used to tune rules.
  Failures on it are reported, not patched to match.
* Languages: English, Hinglish (romanised Hindi), Hindi, Marathi, and one sample each of Bengali,
  Tamil, Urdu, Gujarati, Punjabi (Gurmukhi), Telugu. Several are OCR-noisy (O/0, l/1, S/5, B/8 confusions).

File layout: header lines (`id`, `split`, `lang`, `tags`), `---text---` (document exactly as the engine sees it),
`---expect---`, then one expectation per line:

    field <name>: <value>          structured field (dates ISO yyyy-mm-dd, times HH:MM)
    entity <KIND>: <canonical>     PHONE (10 digits), VEHICLE (compact upper), AADHAAR (12 digits), PAN, ACCOUNT (digits),
                                   IFSC, UPI (lower), EMAIL (lower), AMOUNT (integer rupees), DATE (ISO), SECTION (ACT:number)
    person: <name> | <ROLE>        COMPLAINANT ACCUSED VICTIM WITNESS RELATIVE INVESTIGATING_OFFICER
    rel: <src> | <REL> | <tgt>     OWNS USED CALLED TRANSFERRED_TO ... between a person and an identifier/person
    translate: <english word>      must appear in the English view (translation term recall)

Only items listed are scored for recall; predicted items of a scored kind that are not listed count as
false positives, so the gold lists every phone / date / amount / section that appears in the text.
