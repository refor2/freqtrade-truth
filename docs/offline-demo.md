# Offline synthetic demo (no exchange key, no account access)

To inspect the project without an API key or network request:

```bash
python -m pip install -e .
freqtrade-truth demo
freqtrade-truth demo --json
```

The offline demo runs the **existing** deterministic grouping and comparison
engine over three small synthetic pairs: equal values, an out-of-tolerance
difference and a missing exchange value. It uses the reported net-PnL field
only. JSON output is deterministic, intended for reviewers and CI checks.

Expected classifications:

| Case | Numeric preview status | Difference | Verified owner? |
| --- | --- | --- | --- |
| Equal synthetic amounts | `match` | `0.00` | **No** |
| Different synthetic amounts | `review` | `-0.20` | **No** |
| Unknown exchange amount | `incomplete` | `null` | **No** |

**Critical limitation:** `match` is an existing, pre-alpha *numeric comparison*
status — it is **not proof that two records represent the same economic trade**.
The preview groups records by instrument, currency and close time. Genuine
cross-source order/account/position ownership and complete ledger attribution
remain under public issue [#15](https://github.com/refor2/freqtrade-truth/issues/15).
Do not use this demo or preview for trading decisions, accounting or proof of
exchange settlement.

The command never creates an exchange transport or reads credentials. It does
not run the optional `bybit-check` path; the offline sample is NOT a real Bybit
(or OKX) account result. Source input values are deliberately synthetic.
For live read-only behavior and explicit requirements, see
[First-run walkthrough](first-run.md) and [Reconciliation](reconciliation.md).
