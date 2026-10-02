## Summary

Describe the public-facing change.

## Public safety checklist

- [ ] I reviewed the complete diff.
- [ ] This change contains no credentials, tokens, private keys, cookies, or secrets.
- [ ] This change contains no production IPs, hostnames, internal URLs, account identifiers, or infrastructure details.
- [ ] This change contains no real trades, orders, balances, logs, screenshots, or production exports.
- [ ] This change contains no proprietary strategy, signal, risk, ranking, admission, correlation, or unpublished research logic.
- [ ] Test and example data are fully synthetic.
- [ ] No content was copied from a private repository without explicit public-release review.
- [ ] `python scripts/public_safety_check.py` passes.
- [ ] `python scripts/commit_metadata_check.py` passes for protected project identities.
- [ ] If anything was uncertain, I excluded it rather than publishing it.
