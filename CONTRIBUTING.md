# Contributing

Thanks for helping improve Multi-Trading.

## Ground Rules

- Do not commit real broker credentials, API keys, webhook secrets, tokens, or
  user account data.
- Keep trading changes conservative by default. New live-trading behavior
  should include clear risk controls, configuration switches, and tests where
  practical.
- Preserve third-party license notices when adding or modifying dependencies.
- Do not paste proprietary code or datasets unless you have the right to
  contribute them under this repository's license.

## Development Checklist

1. Create a branch for your change.
2. Keep the change focused and explain the trading or workflow impact.
3. Run relevant tests or document why they were not run.
4. Update README, `.env.example`, or docs when changing setup, configuration,
   permissions, or user-facing behavior.
5. For broker, order, notification, or automation changes, include a dry-run or
   test-mode path when possible.

## Licensing of Contributions

Unless explicitly stated otherwise, contributions submitted to this repository
are licensed under the Apache License, Version 2.0, the same license as the
project.
