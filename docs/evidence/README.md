# Evidence

This directory holds **generated** M0 evidence. The generated files are
gitignored on purpose — evidence is reproduced from source, not committed, so it
can never drift from the code.

## Regenerate

```bash
python scripts/prove_m0.py --out docs/evidence/m0_proof.json
```

This runs the full acceptance flow against deterministic **fixtures** (no vendor
or network access required) and writes `m0_proof.json`, plus a PASS/FAIL summary
on stderr. Because the flow is deterministic (fixed observation clock, fixed
fixtures), the SHA-256 hashes and manifest digest are stable across runs.

The evidence is explicitly **fixture** proof (`"mode": "FIXTURE"`), never
presented as live-vendor proof.
