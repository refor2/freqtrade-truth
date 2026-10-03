# Deterministic reconciliation

The reconciliation core compares normalized financial records only. It does not query exchanges,
infer unavailable components, convert currencies, or mutate accounts.

## Grouping first

Records are grouped before financial values are compared.

A group is partitioned by exact canonical instrument and settlement currency. Opposite-source
records are connected only when their close timestamps fall within the configured close-time
tolerance.

Ambiguous components remain ambiguous. The core does not choose a nearest trade or invent a
one-to-one pairing.

## Statuses

Each group result has one of three statuses:

- `MATCH` — every selected financial field is present and within tolerance.
- `REVIEW` — at least one known value is outside tolerance, or the group has multiple
  Freqtrade records and cannot be safely assigned.
- `INCOMPLETE` — no known mismatch exists, but required records or selected financial values
  are missing.

A known mismatch takes precedence over incomplete fields so a concrete discrepancy cannot be
hidden by unrelated missing data.

## Field comparison

For a structurally comparable group, there is exactly one Freqtrade record and one or more
exchange records.

For each selected field:

1. The Freqtrade value must be present.
2. The field must be present on every exchange record in the group.
3. Exchange values are summed with `Decimal`.
4. `difference = exchange_value - freqtrade_value`.
5. The field matches when `abs(difference) <= absolute_tolerance`.

If any exchange component is missing, the core does not sum the remaining values. The result for
that field is `INCOMPLETE`.

The default field order is deterministic:

1. price PnL,
2. trading fees,
3. funding,
4. other adjustments,
5. reported net PnL.

## Reason codes

Results expose machine-readable reason codes:

- `value_within_tolerance`
- `value_outside_tolerance`
- `missing_freqtrade_value`
- `missing_exchange_value`
- `missing_freqtrade_record`
- `missing_exchange_record`
- `ambiguous_freqtrade_records`

Reason codes describe evidence; they do not guess at causes.

## Funding ledger evidence

Funding evidence is collected separately from final reconciliation attribution.

A funding evidence window is considered usable only when:

- the reconciliation group contains exactly one Freqtrade record and at least one exchange record,
- the Freqtrade record has a known open time,
- the declared ledger coverage fully spans the trade open/close interval,
- funding events have the same settlement currency,
- funding events expose the exact canonical instrument identity.

Exact-instrument funding events inside the trade interval are summed with `Decimal` and returned
as `candidate_funding`. This is evidence, not final ownership attribution.

If a funding-bearing ledger event inside the interval has no instrument identity, the evidence is
`AMBIGUOUS` and no funding total is claimed. If ledger coverage does not span the whole trade
window, the evidence is `INCOMPLETE`; a partial sum is never returned.

A complete window with no funding-bearing event returns `NO_EVENTS` with a zero candidate. This
means no funding event was present in the supplied complete ledger window; it does not establish
that a particular account or position ownership assumption is valid.

The public core deliberately does not infer that every event for a symbol belongs to the bot.
Guarded attribution remains a separate milestone.

## Bybit limitation

The Bybit inverse closed-PnL endpoint provides reported closed PnL and, when present, opening and
closing fees. It does not expose funding or price-only PnL as separate closed-trade fields.

Those unavailable components therefore remain `INCOMPLETE` until a separate, explicit ledger
enrichment / attribution layer can provide trustworthy normalized values. The comparison core
does not derive funding or price PnL by subtraction.
