"""A complete native event candidate must never assert cross-account ownership."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any

import pytest

from freqtrade_truth.core.settlement_ledger import audit_settlement_ledger


def snapshot() -> dict[str, Any]:
    event = lambda eid, kind, amount: {
        'id': eid,
        'position_ref': 'synthetic-position',
        'instrument': 'BTC/USD:BTC',
        'currency': 'BTC',
        'occurred_at': '2030-01-01T12:00:00Z',
        'kind': kind,
        'amount': amount,
    }
    return {
        'schema': 'freqtrade-truth.settlement-ledger.v1',
        'trade': {
            'trade_ref': 'synthetic-bot',
            'position_ref': 'synthetic-position',
            'instrument': 'BTC/USD:BTC',
            'settlement_currency': 'BTC',
            'opened_at': '2030-01-01T00:00:00Z',
            'closed_at': '2030-01-02T00:00:00Z',
            'bot_net_pnl': '9.50',
            'exchange_reported_net_pnl': '9.50',
        },
        'coverage': {
            'start_at': '2030-01-01T00:00:00Z',
            'end_at': '2030-01-02T00:00:00Z',
            'price_complete': True,
            'fees_complete': True,
            'funding_complete': True,
            'other_complete': True,
        },
        'events': [
            event('partial-1', 'price_pnl', '6.25'),
            event('partial-2', 'price_pnl', '4.00'),
            event('fill-1', 'trading_fee', '-0.25'),
            event('fill-2', 'trading_fee', '-0.25'),
            event('funding-1', 'funding', '-0.25'),
        ],
    }


def test_full_native_event_equation_is_candidate_only_and_redacted() -> None:
    r = audit_settlement_ledger(snapshot(), tolerance=Decimal('0'))
    assert r['numeric_status'] == 'match'
    assert r['native_components'] == {
        'price_pnl': '10.25', 'trading_fees': '-0.50',
        'funding': '-0.25', 'other_adjustments': '0',
    }
    assert r['computed_native_net'] == '9.50'
    assert r['exchange_minus_bot'] == '0.00'
    assert r['account_verified'] is False
    assert r['verified_financial_truth'] is False
    assert 'synthetic-bot' not in str(r)
    assert 'synthetic-position' not in str(r)


def test_net_mismatch_takes_precedence_over_missing_funding() -> None:
    p = snapshot()
    assert isinstance(p['trade'], dict) and isinstance(p['coverage'], dict)
    p['trade']['bot_net_pnl'] = '9.40'
    p['coverage']['funding_complete'] = False
    r = audit_settlement_ledger(p, tolerance=Decimal('0'))
    assert r['numeric_status'] == 'review'
    assert r['computed_native_net'] is None
    assert r['exchange_minus_bot'] == '0.10'
    assert 'funding_source_incomplete' in r['reasons']


def test_missing_funding_zero_only_with_complete_coverage() -> None:
    p = snapshot()
    assert isinstance(p['events'], list)
    p['events'] = [e for e in p['events'] if e['kind'] != 'funding']
    assert isinstance(p['trade'], dict) and isinstance(p['coverage'], dict)
    p['trade']['bot_net_pnl'] = '9.75'
    p['trade']['exchange_reported_net_pnl'] = '9.75'
    assert audit_settlement_ledger(p, tolerance=Decimal('0'))['native_components']['funding'] == '0'
    p['coverage']['funding_complete'] = False
    r = audit_settlement_ledger(p, tolerance=Decimal('0'))
    assert r['numeric_status'] == 'incomplete'
    assert r['native_components']['funding'] is None


def test_native_exchange_equation_is_checked_separately() -> None:
    p = snapshot()
    assert isinstance(p['trade'], dict)
    p['trade']['exchange_reported_net_pnl'] = '9.49'
    result = audit_settlement_ledger(p, tolerance=Decimal('0'))
    assert result['numeric_status'] == 'review'
    assert result['computed_minus_exchange'] == '0.01'


@pytest.mark.parametrize('problem,reason', [
    ('duplicate', 'duplicate_event_id'),
    ('other_position', 'foreign_position_event'),
    ('other_instrument', 'instrument_mismatch'),
    ('other_currency', 'currency_mismatch'),
    ('outside_trade', 'event_outside_trade_window'),
    ('unrecognized_kind', 'unsupported_event_kind'),
])
def test_foreign_events_are_ambiguous_not_silently_filtered(problem: str, reason: str) -> None:
    p = snapshot()
    assert isinstance(p['events'], list)
    event = deepcopy(p['events'][0])
    if problem == 'other_position':
        event['position_ref'] = 'unrelated-position'
    elif problem == 'other_instrument':
        event['instrument'] = 'ETH/USD:BTC'
    elif problem == 'other_currency':
        event['currency'] = 'USD'
    elif problem == 'outside_trade':
        event['occurred_at'] = '2030-01-03T00:00:00Z'
    elif problem == 'unrecognized_kind':
        event['kind'] = 'unexpected_kind'
    p['events'].append(event)
    r = audit_settlement_ledger(p, tolerance=Decimal('0'))
    assert r['numeric_status'] == 'ambiguous'
    assert reason in r['reasons']
    assert r['computed_native_net'] is None


@pytest.mark.parametrize('amount', ['NaN', 'Infinity', '-Infinity', '1e10000', 1.25, 'not-a-number'])
def test_invalid_money_rejected_without_echoing_value(amount: object) -> None:
    p = snapshot()
    assert isinstance(p['events'], list)
    p['events'][0]['amount'] = amount
    with pytest.raises(ValueError, match='invalid monetary value'):
        audit_settlement_ledger(p, tolerance=Decimal('0'))


def test_missing_net_never_becomes_zero() -> None:
    p = snapshot()
    assert isinstance(p['trade'], dict)
    p['trade']['bot_net_pnl'] = None
    r = audit_settlement_ledger(p, tolerance=Decimal('0'))
    assert r['numeric_status'] == 'incomplete'
    assert r['exchange_minus_bot'] is None


def test_positive_fee_rebate_and_funding_credit_preserve_equity_sign() -> None:
    p = snapshot()
    assert isinstance(p['events'], list) and isinstance(p['trade'], dict)
    p['events'][2]['amount'] = '0.25'
    p['events'][4]['amount'] = '0.25'
    p['trade']['bot_net_pnl'] = '10.50'
    p['trade']['exchange_reported_net_pnl'] = '10.50'
    assert audit_settlement_ledger(p, tolerance=Decimal('0'))['numeric_status'] == 'match'


def test_different_settlement_currency_is_not_converted_to_usd() -> None:
    p = snapshot()
    assert isinstance(p['trade'], dict)
    p['trade']['settlement_currency'] = 'USDC'
    r = audit_settlement_ledger(p, tolerance=Decimal('0'))
    assert r['numeric_status'] == 'ambiguous'
    assert r['usd_verified'] is False


def test_incomplete_time_coverage_is_not_a_complete_evidence_set() -> None:
    p = snapshot()
    assert isinstance(p['coverage'], dict)
    p['coverage']['start_at'] = '2030-01-01T04:00:00Z'
    r = audit_settlement_ledger(p, tolerance=Decimal('0'))
    assert r['numeric_status'] == 'incomplete'
    assert 'ledger_coverage_incomplete' in r['reasons']
    p['coverage']['start_at'] = '2030-01-01T00:00:00+02:00'
    with pytest.raises(ValueError, match='timestamp must be UTC'):
        audit_settlement_ledger(p, tolerance=Decimal('0'))


def test_coverage_claim_must_be_literal_bool_and_tolerance_finite_decimal() -> None:
    p = snapshot()
    assert isinstance(p['coverage'], dict)
    p['coverage']['fees_complete'] = 'yes'
    with pytest.raises(ValueError, match='coverage completeness'):
        audit_settlement_ledger(p, tolerance=Decimal('0'))
    with pytest.raises(ValueError, match='tolerance'):
        audit_settlement_ledger(snapshot(), tolerance=Decimal('NaN'))


def test_unknown_schema_and_additional_top_level_fields_rejected() -> None:
    p = snapshot()
    p['sensitive'] = 'must-not-be-echoed'
    with pytest.raises(ValueError, match='invalid settlement envelope'):
        audit_settlement_ledger(p, tolerance=Decimal('0'))


def test_excessive_event_count_rejected() -> None:
    p = snapshot()
    assert isinstance(p['events'], list)
    p['events'] *= 201
    with pytest.raises(ValueError, match='event count'):
        audit_settlement_ledger(p, tolerance=Decimal('0'))


def test_unsupported_event_names_do_not_appear_in_redacted_output() -> None:
    p = snapshot()
    assert isinstance(p['events'], list)
    p['events'][0]['kind'] = 'unrecognized-private-value'
    result = audit_settlement_ledger(p, tolerance=Decimal('0'))
    assert result['numeric_status'] == 'ambiguous'
    assert 'unrecognized-private-value' not in str(result)


def test_noncurrency_label_is_rejected_before_output() -> None:
    p = snapshot()
    assert isinstance(p['trade'], dict)
    p['trade']['settlement_currency'] = 'SENSITIVE_VALUE'
    with pytest.raises(ValueError, match='invalid settlement currency'):
        audit_settlement_ledger(p, tolerance=Decimal('0'))


def test_unhashable_kind_returns_ambiguous_instead_of_exception() -> None:
    p = snapshot()
    assert isinstance(p['events'], list)
    p['events'][0]['kind'] = ['bad-kind']
    assert audit_settlement_ledger(p, tolerance=Decimal('0'))['numeric_status'] == 'ambiguous'


def test_empty_price_events_never_prove_a_closed_position_price_pnl() -> None:
    p = snapshot()
    assert isinstance(p['events'], list)
    p['events'] = [e for e in p['events'] if e['kind'] != 'price_pnl']
    r = audit_settlement_ledger(p, tolerance=Decimal('0'))
    assert r['numeric_status'] == 'incomplete'
    assert 'price_source_missing' in r['reasons']


def test_extreme_precision_does_not_round_fee_when_summing_event_ledger() -> None:
    p = snapshot()
    assert isinstance(p['events'], list) and isinstance(p['trade'], dict)
    p['events'][2]['amount'] = '-0.00000000000000000000000000000001'
    p['events'][3]['amount'] = '-0.00000000000000000000000000000001'
    p['events'][4]['amount'] = '0'
    p['trade']['bot_net_pnl'] = '10.24999999999999999999999999999998'
    p['trade']['exchange_reported_net_pnl'] = '10.24999999999999999999999999999998'
    r = audit_settlement_ledger(p, tolerance=Decimal('0'))
    assert r['numeric_status'] == 'match'
    assert r['computed_native_net'] == '10.24999999999999999999999999999998'
