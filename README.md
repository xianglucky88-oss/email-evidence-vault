# Email Evidence Vault

Reconstruct email threads and index deduplicated attachments under legal hold.

## Modules

- `src/email_evidence_vault/models.py`: immutable result contract.
- `src/email_evidence_vault/parser.py`: input validation and record extraction.
- `src/email_evidence_vault/engine.py`: domain calculation only.
- `src/email_evidence_vault/report.py`: stable output envelope.
- `src/email_evidence_vault/pipeline.py`: orchestration boundary.
- `src/email_evidence_vault/cli.py`: deterministic JSON CLI.

## Run

```bash
PYTHONPATH=src python -m email_evidence_vault.cli
PYTHONPATH=src python -m unittest discover -s tests -v
```

The fixture is local and deterministic; no network or credentials are required.
