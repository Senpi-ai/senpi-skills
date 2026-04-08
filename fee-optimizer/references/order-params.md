# Standard Order Params (Fee Optimizer)

Canonical order type and options per context. Use these when building order payloads for Senpi `create_position`, `close_position`, or `edit_position`.

## Entry (planned — guaranteed fill)

Use for normal planned entries where you want maker rate but are willing to fall back to taker if not filled in time.

- **orderType:** `"FEE_OPTIMIZED_LIMIT"`
- **feeOptimizedLimitOptions:** `{ "ensureExecutionAsTaker": true }`
- Optional: `executionTimeoutSeconds` (integer 1–300), e.g. `45` (server default) or `20` for faster fallback.

Example:
```json
{
  "orderType": "FEE_OPTIMIZED_LIMIT",
  "feeOptimizedLimitOptions": {
    "ensureExecutionAsTaker": true,
    "executionTimeoutSeconds": 45
  }
}
```

## Entry (resting — maker only)

Use when you want pure maker, no taker fallback. You must monitor open orders and cancel if needed.

- **orderType:** `"FEE_OPTIMIZED_LIMIT"`
- **feeOptimizedLimitOptions:** omit entirely.

Example:
```json
{
  "orderType": "FEE_OPTIMIZED_LIMIT"
}
```

## Exit: take-profit / scaling out

Use ALO when closing in profit and timing is not critical.

- **orderType:** `"FEE_OPTIMIZED_LIMIT"`
- **feeOptimizedLimitOptions:** optional; use `{ "ensureExecutionAsTaker": true }` if you want guaranteed fill within timeout.

## Exit: stop-loss / emergency

Always use market. No ALO.

- **orderType:** `"MARKET"`
- **feeOptimizedLimitOptions:** must not be set.

Example:
```json
{
  "orderType": "MARKET"
}
```

## Summary

| Context        | orderType              | feeOptimizedLimitOptions                    |
|----------------|------------------------|---------------------------------------------|
| Entry (planned)| FEE_OPTIMIZED_LIMIT    | ensureExecutionAsTaker: true, optional timeout |
| Entry (resting)| FEE_OPTIMIZED_LIMIT    | omit                                        |
| Exit TP        | FEE_OPTIMIZED_LIMIT    | optional ensureExecutionAsTaker              |
| Exit SL/emergency | MARKET              | omit (not applicable)                       |
