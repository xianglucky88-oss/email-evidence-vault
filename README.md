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

## Evidence index maintenance (handover)

- `docs/evidence-index-maintenance-manual.md`: index rules, failure modes,
  field lineage, review checklist, rollback, and escalation.
- `docs/index-change-register.md`: REQ gaps, change records, hold events,
  rollbacks, and sign-off.
- `docs/samples/`: 12 minimal cases plus the frozen `v0.1.0` baseline.
- `tools/index_audit.py`: read-only audit harness.

```bash
python3 tools/index_audit.py check     # must report 0 drift, rules=unchanged
python3 tools/index_audit.py inspect   # documented anomaly profile per case
python3 tools/index_audit.py run --out /tmp/before
python3 tools/index_audit.py compare --before /tmp/before --after /tmp/after
```

`check` fails when the sample outputs drift **or** when `rules_sha256` differs
from the frozen baseline, because some rule changes (for example replacing the
hash algorithm) leave the current output shape unchanged.
