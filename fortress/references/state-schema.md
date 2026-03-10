# Fortress State Schema

Fortress state files are JSON objects written atomically via `os.replace()`.

## Path Layout

```text
{workspace}/
├── fortress-config.json
└── state/
    └── fortress-default/
        ├── oracle-vote.json
        ├── ta-vote.json
        ├── vol-vote.json
        └── risk-vote.json
```

## Common Fields

Every state file should include:

```json
{
  "version": 1,
  "active": true,
  "instanceKey": "fortress-default",
  "createdAt": "2026-02-27T10:00:00Z",
  "updatedAt": "2026-02-27T10:15:00Z"
}
```

## Vote File Example

```json
{
  "pillar": "oracle",
  "asset": "HYPE",
  "vote": "GO",
  "conviction": 4,
  "reasons": ["leaderflow_stable"],
  "updatedAt": "2026-02-27T10:15:00Z"
}
```

## Percentage Convention

All percentages are whole numbers:

- `5` means `5%`
- do not store `0.05`

Convert internally in code when decimal values are needed.