# Data and document ingestion

This module handles the project's self-contained synthetic investigation data and uploaded evidence/documents.

## Supported uploads

- FIR/report images: PNG, JPG, JPEG, WEBP
- Scanned PDFs (rendered and OCR'd when the PDF has no usable text layer)
- Text PDFs with a native text layer
- DOCX/TXT
- CSV/XLSX tables

## FIR intelligence

`ai_engine/nlp/document_intelligence.py` provides local OCR and role-aware FIR parsing. The first supported languages are English, Hindi and Marathi, including mixed-language OCR. The parser extracts structured fields, entities, conservative relationship candidates and incident-event candidates.

OCR is intentionally not treated as ground truth: confidence and verification warnings are returned with every image/scanned-document extraction.
