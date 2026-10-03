"""Deterministic grouping contracts for financial reconciliation."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from freqtrade_truth.core.models import FinancialField, NormalizedTradeRecord, SourceKind


@dataclass(frozen=True, slots=True)
class ReconciliationTolerance:
    """Explicit time and absolute financial tolerances used by reconciliation."""

    close_time: timedelta = timedelta(0)
    price_pnl: Decimal = Decimal("0")
    trading_fees: Decimal = Decimal("0")
    funding: Decimal = Decimal("0")
    other_adjustments: Decimal = Decimal("0")
    reported_net_pnl: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.close_time < timedelta(0):
            raise ValueError("close_time must not be negative")
        for field_name, value in (
            ("price_pnl", self.price_pnl),
            ("trading_fees", self.trading_fees),
            ("funding", self.funding),
            ("other_adjustments", self.other_adjustments),
            ("reported_net_pnl", self.reported_net_pnl),
        ):
            if not value.is_finite() or value < 0:
                raise ValueError(
                    f"{field_name} tolerance must be a finite non-negative Decimal"
                )

    def for_field(self, field: FinancialField) -> Decimal:
        """Return the configured absolute tolerance for one financial field."""

        if field is FinancialField.PRICE_PNL:
            return self.price_pnl
        if field is FinancialField.TRADING_FEES:
            return self.trading_fees
        if field is FinancialField.FUNDING:
            return self.funding
        if field is FinancialField.OTHER_ADJUSTMENTS:
            return self.other_adjustments
        if field is FinancialField.REPORTED_NET_PNL:
            return self.reported_net_pnl
        raise AssertionError(f"unsupported financial field: {field!r}")


@dataclass(frozen=True, slots=True)
class ComparableRecordGroup:
    """One deterministic connected component of potentially comparable records."""

    instrument: str
    settlement_currency: str
    freqtrade_records: tuple[NormalizedTradeRecord, ...]
    exchange_records: tuple[NormalizedTradeRecord, ...]

    @property
    def is_structurally_comparable(self) -> bool:
        """Whether the group has one bot trade and at least one exchange record."""

        return len(self.freqtrade_records) == 1 and bool(self.exchange_records)


def group_comparable_records(
    freqtrade_records: Sequence[NormalizedTradeRecord],
    exchange_records: Sequence[NormalizedTradeRecord],
    *,
    tolerance: ReconciliationTolerance,
) -> tuple[ComparableRecordGroup, ...]:
    """Group records without guessing across instrument, currency, or ambiguous time links.

    Records are first partitioned by exact normalized instrument and settlement currency.
    Within each partition, a bipartite edge exists when the absolute close-time difference
    is within ``tolerance.close_time``. Connected components are returned intact so that
    ambiguous many-to-many matches remain visible instead of being silently assigned.
    """

    freqtrade = _validated_records(
        freqtrade_records,
        SourceKind.FREQTRADE,
        "freqtrade_records",
    )
    exchange = _validated_records(
        exchange_records,
        SourceKind.EXCHANGE,
        "exchange_records",
    )

    partitions: dict[
        tuple[str, str],
        tuple[list[NormalizedTradeRecord], list[NormalizedTradeRecord]],
    ] = defaultdict(lambda: ([], []))

    for record in freqtrade:
        partitions[(record.instrument, record.settlement_currency)][0].append(record)
    for record in exchange:
        partitions[(record.instrument, record.settlement_currency)][1].append(record)

    groups: list[ComparableRecordGroup] = []
    for key in sorted(partitions):
        bot_records, exchange_side = partitions[key]
        groups.extend(
            _partition_components(
                instrument=key[0],
                settlement_currency=key[1],
                freqtrade_records=bot_records,
                exchange_records=exchange_side,
                close_time_tolerance=tolerance.close_time,
            )
        )

    groups.sort(key=_group_sort_key)
    return tuple(groups)


def _validated_records(
    records: Sequence[NormalizedTradeRecord],
    expected_kind: SourceKind,
    field_name: str,
) -> tuple[NormalizedTradeRecord, ...]:
    normalized = tuple(records)
    seen: set[tuple[str, str]] = set()
    for record in normalized:
        if record.source_kind is not expected_kind:
            raise ValueError(f"{field_name} contains a record with the wrong source kind")
        identity = (record.source_name, record.record_id)
        if identity in seen:
            raise ValueError(f"{field_name} contains a duplicate source record")
        seen.add(identity)
    return tuple(sorted(normalized, key=_record_sort_key))


def _partition_components(
    *,
    instrument: str,
    settlement_currency: str,
    freqtrade_records: Sequence[NormalizedTradeRecord],
    exchange_records: Sequence[NormalizedTradeRecord],
    close_time_tolerance: timedelta,
) -> list[ComparableRecordGroup]:
    left = tuple(sorted(freqtrade_records, key=_record_sort_key))
    right = tuple(sorted(exchange_records, key=_record_sort_key))
    nodes = tuple((0, index) for index in range(len(left))) + tuple(
        (1, index) for index in range(len(right))
    )
    unvisited = set(nodes)
    components: list[ComparableRecordGroup] = []

    while unvisited:
        start = min(unvisited, key=lambda node: _node_sort_key(node, left, right))
        queue = deque([start])
        unvisited.remove(start)
        left_indexes: set[int] = set()
        right_indexes: set[int] = set()

        while queue:
            side, index = queue.popleft()
            if side == 0:
                left_indexes.add(index)
                current = left[index]
                candidates = ((1, other_index) for other_index in range(len(right)))
            else:
                right_indexes.add(index)
                current = right[index]
                candidates = ((0, other_index) for other_index in range(len(left)))

            for neighbor in candidates:
                if neighbor not in unvisited:
                    continue
                neighbor_record = _node_record(neighbor, left, right)
                if abs(current.closed_at - neighbor_record.closed_at) <= close_time_tolerance:
                    unvisited.remove(neighbor)
                    queue.append(neighbor)

        components.append(
            ComparableRecordGroup(
                instrument=instrument,
                settlement_currency=settlement_currency,
                freqtrade_records=tuple(left[index] for index in sorted(left_indexes)),
                exchange_records=tuple(right[index] for index in sorted(right_indexes)),
            )
        )

    return components


def _node_record(
    node: tuple[int, int],
    left: Sequence[NormalizedTradeRecord],
    right: Sequence[NormalizedTradeRecord],
) -> NormalizedTradeRecord:
    side, index = node
    return left[index] if side == 0 else right[index]


def _node_sort_key(
    node: tuple[int, int],
    left: Sequence[NormalizedTradeRecord],
    right: Sequence[NormalizedTradeRecord],
) -> tuple[object, ...]:
    record = _node_record(node, left, right)
    return (*_record_sort_key(record), node[0])


def _record_sort_key(record: NormalizedTradeRecord) -> tuple[object, ...]:
    return (record.closed_at, record.source_name, record.record_id)


def _group_sort_key(group: ComparableRecordGroup) -> tuple[object, ...]:
    records = (*group.freqtrade_records, *group.exchange_records)
    first = min(records, key=_record_sort_key)
    return (
        first.closed_at,
        group.instrument,
        group.settlement_currency,
        tuple(record.record_id for record in group.freqtrade_records),
        tuple(record.record_id for record in group.exchange_records),
    )
