# Agent instructions

## Versioning

Every pull request bumps this repository's version in the same PR: patch for a fix or a refactor, minor for a feature or a visible change, major for a breaking change. Stacked PRs bump in order. Put the new version in the PR description and state the bump (for example `1.0.0 → 1.0.1`).

Where the version lives: `nozle/_version.py` (`pyproject.toml` reads it).

## Testing safety rules

These apply to every test, script and agent working in this repository, locally, on the test VM or against production.

- **Never send a raw card number to a payment provider API**, not even a public Stripe test number in test mode. Stripe refuses it and emails the account owner a PCI warning. From scripts use only test tokens (`pm_card_visa`, `pm_card_createDispute`, `tok_*`). A test card that has no token is typed into Stripe's hosted page or Payment Element in a browser.
- **Test keys only.** Use `sk_test_` / `rzp_test_` keys for automated tests. Check the key prefix in the script before any provider call. Never print, log or write a secret key, password or token to a file, a commit or a chat.
- **No real money without the owner's approval.** A real charge, refund or payout on a live key needs explicit approval for that exact action.
- **Production is read-only by default.** Any write on production (data, configuration, deployments, plan prices, customer state) needs the owner's approval for that action, and must be scoped to test workspaces and test customers. Put back what you changed.
- **Test identities.** Test users and customers use `prathammehta1026+<tag>@gmail.com`. Never send email to any other real address. Mail on the test VM stays in its Mailhog.
- **No outside services for payloads.** Do not send customer or payment data to third-party request catchers or paste sites unless the owner asked for it.
- **Ask before anything irreversible or outward facing**: pushing, opening or merging pull requests, publishing packages, deleting data, contacting people.
