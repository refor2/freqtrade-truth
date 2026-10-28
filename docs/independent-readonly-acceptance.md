# Independent financial evidence acceptance — v0.1 HOLD

**Status: executable source preview, not accepted financial accounting.**
This checklist is a controlled path to reviewing [issue #15](https://github.com/refor2/freqtrade-truth/issues/15),
not a claim that a Freqtrade trade equals an exchange position or that v0.1 may be released.

## A. Reproducible, credential-free acceptance

On an independent machine with Python 3.11–3.14, install an exact reviewed
GitHub commit in an isolated virtual environment. Run:

```sh
freqtrade-truth --help
freqtrade-truth demo --json
freqtrade-truth reconcile-bybit --help
python -m pytest
python scripts/public_safety_check.py
python scripts/commit_metadata_check.py
python scripts/release_integrity_check.py
```

The last release-integrity command requires locally built `dist/` artifacts.
These steps verify a package and **synthetic behavior**, not an account ledger.
For an independent package install, obtain the reviewed CI wheel artifact and
verify its SHA-256 before installing; do not assume working-tree tests validate
the wheel. Never include account samples in public CI or issues.

## B. Optional, locally authorized account source check

Only an operator of their **own** independent Freqtrade instance and Bybit
account may perform this. Use a separate exchange key configured strictly
**Read-only**, preferably IP-restricted; keep it in the local secret store.
Use the account's actual canonical inverse instrument/settlement. Never pass
credentials in command arguments, URLs, screenshots, CI environment, issues,
or this repository. Keep all raw records and monetary output locally.

```sh
freqtrade-truth reconcile-bybit --freqtrade-url http://127.0.0.1:8080 \
    --symbol BTCUSD --base BTC --quote USD --settle BTC \
    --hours 24 --limit 50 --max-pages 20 --json \
    --check-order-links --check-execution-ledger
```

This is an *example* for a genuinely supported BTC inverse contract, not a
recommendation to use a particular exchange or to trade. The CLI verifies
read-only key permission before private Bybit queries, performs only approved
GET operations and emits redacted **candidate-only** counts. Missing API
access, a reached page/record limit, malformed source, or an inconsistent
source query window is a failure. Never reclassify it as a passed account
reconciliation by changing the time window or ignoring events.

## C. Financial-truth proof obligations (currently unverified)

The independent reviewer needs private, separately audited evidence of:

1. **Account identity**: immutable exchange account/subaccount scope,
   bot execution-order provenance and all competing manual/automated trades.
2. **Full economic lifecycle**: trade/position entry, every partial fill,
   partial exit, cancellations, position reuse and exact final closure.
3. **Exact fee ledger**: unique execution ↔ account bill references, signed
   paid fees/rebates, native currency, no duplicated or missing events.
4. **Funding attribution**: each signed funding event belongs to precisely
   one proven lifecycle; shared symbol/time alone is not an ownership proof.
5. **Retention and completeness**: each necessary ledger and execution query
   covers the real lifecycle, traverses all pages, and is inside the exchange's
   supported historical retention. Cursor exhaustion **does not** prove
   retained history is complete. The CLI's default close-date window may be
   shorter than a position's true opening-to-closing lifetime.
6. **Native net and optional USD valuation**: independently sourced,
   non-overlapping price PnL, signed fees, funding and adjustments in one
   currency; no addition of already-inclusive reported net. For cross-currency
   comparisons require independently sourced event-time FX rates and
   a documented conversion convention. USD and USDC are **not** assumed equal.
7. **Adversarial cases**: two bots on the same instrument/time, fee rebates,
   missed account bills, repeated IDs, position ID reuse, partial fills,
   mixed account modes, truncation and absent values must all fail closed.

All proof material stays in the operator's private authorized environment.
Public conclusions can state only non-identifying counts and which gates
passed or failed, without source IDs, addresses, account details or amounts.
Numerical equality is not independently verified net PnL. A successful test
run, GET or positive candidate link does **not** close issue #15.

## D. Public release governance — issue #18

Before the first stable tag, repository admins must configure actual rules for
`main` through GitHub Settings → Branches/Rulesets: pull requests required,
no force pushes or deletion, and mandatory green public-safety, package,
static-quality and Python 3.11/3.12/3.13/3.14 jobs. Verify their enforcement
against an unqualified PR and confirm release documentation matches the
observed GitHub settings. Merely documenting these protections does not enable
them; the connected app may not have repository administration/ruleset APIs.

**Stop condition:** until #15 has independent real-account financial
acceptance and #18 is enforced, the PR stays draft/unmerged and no stable
v0.1 tag or certified account-level financial report is authorized.
