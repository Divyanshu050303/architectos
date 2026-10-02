# Payments architecture

## Components

- `payments-api` validates and authorizes card payments.
- `ledger-db` records every movement of money (append-only).
- `fraud-scorer` checks risk before authorization.

## Data flow

Checkout calls `payments-api`, which calls `fraud-scorer` and then the card network.
Every authorization is written to `ledger-db` before checkout is told the result.

## Security

Card numbers are never stored: the card network returns a token, and only the token
is kept (PCI DSS scope reduction). All traffic uses TLS 1.2 or later; certificates are
rotated every 90 days.

## Decisions

The ledger is append-only, as decided in ADR-7: corrections are new entries, never
updates.
