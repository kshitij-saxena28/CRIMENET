# Cross-FIR interlink demo

Use `FIR_LINK_01.txt` and `FIR_LINK_02.txt` to test the FIR↔FIR relationship workflow.

Both FIRs intentionally share these evidence keys:
- Phone: 9876543210
- Vehicle: UP14 AB 4521
- Account: ACCT-445566
- Serial: SN-CRIME-7788

They use different FIR numbers, police stations, complainants and dates so the UI can demonstrate that a relationship is based on explainable shared identifiers rather than silently merging the people.

Recommended workflow:
1. In Ingestion & NLP, upload both files into the same investigation workspace.
2. Process both documents.
3. Review and verify both FIRs.
4. Open Signals + Evidence or Analytics and inspect FIR↔FIR relationship intelligence.
5. Expected result: a strong candidate/verified graph link with the shared keys listed above.
6. In AI Copilot, ask `Show FIR relationships` or use the generated cross-FIR recommendation.

These are synthetic demo records. They are not real police records.
