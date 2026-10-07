# Interconnected FIR samples

These two synthetic text FIRs are ready for a two-FIR link demonstration.

Upload both files into the **same `[FIR]` investigation workspace**, process them, then verify both documents. The FIR Link Center should expose an explainable relationship based on shared evidence keys.

Shared keys intentionally included in both FIRs:
- Phone: `9876543210`
- Vehicle: `UP14 AB 4521`
- Property serial: `SN-CRIME-7788`
- Account: `ACCT-445566`

The FIR numbers, complainants, police stations, dates and incident locations are different. The records are synthetic and do not represent real police records.

Expected workflow:
1. Ingestion & NLP → upload both files together.
2. Process all documents.
3. Review/verify both FIRs.
4. Open **FIR Link Center**.
5. Confirm the pair shows as a strong, explainable relationship and the shared keys are listed.
6. Open **Knowledge Graph** to see the `INTERLINKED_VIA` connection.
7. Ask Copilot: `Show FIR relationships`.
