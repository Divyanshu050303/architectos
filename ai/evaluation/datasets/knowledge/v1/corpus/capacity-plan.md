# Capacity plan

## Targets

Checkout must sustain 2,000 requests per second at peak (REQ-12) with a p95 latency
under 300 ms.

## Caching

Product catalog reads are served from Redis (`catalog-cache`) with a 5 minute TTL,
which removes about 80% of catalog reads from the database.

## Database

The orders database handles writes on one primary; reads go to two read replicas.
