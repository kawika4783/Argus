# Evidence workflow

ARGUS now provides a case-scoped evidence registry.

- Supported uploads: PDF, TXT, CSV, JSON, XLSX, DOCX, JPG, JPEG, and PNG.
- Each upload is stored as a non-executable object under the configured evidence volume.
- The API records the original filename, source reference, analyst notes, byte count, MIME type, upload time, and SHA-256 digest.
- PDF and image uploads must match their basic file signatures. All uploads are size-limited by `NEXUSINTEL_EVIDENCE_MAX_BYTES` (25 MB by default).
- Upload, list, and download requests enforce case ownership. Administrators can access evidence across cases.
- Browser downloads use an authorization-checked API route; storage paths are never sent to the browser.

The current milestone uses the existing Docker volume `nexusintel_evidence`. Virus scanning, MinIO object storage, document text extraction, and evidence-to-entity linking remain later hardening milestones.
