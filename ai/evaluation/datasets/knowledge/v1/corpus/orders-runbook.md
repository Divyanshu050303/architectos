# Orders runbook

The orders service (`orders-api`) stores orders in PostgreSQL (`orders-db`).

## Failover

When the primary orders database is unreachable, promote the read replica with
`pg_ctl promote` and point `orders-api` at it. Promotion must finish within 30 s.
Replicas lag at most 2 s behind the primary.

## Backups

Full backups of `orders-db` run nightly at 02:00 UTC. Point-in-time recovery is
possible for the last 7 days of write-ahead logs.

## Scaling

`orders-api` scales horizontally from 3 to 12 pods on CPU above 70%.

## Incident contacts

The on-call engineer for orders is paged through the incident tool. Escalate to the
payments team when checkout fails.
