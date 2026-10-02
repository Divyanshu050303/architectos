# Order events

## Topics

`order-events` carries every order state change. It has 12 partitions; the order id
is the message key, so all events of one order are ordered.

## Consumers

Each downstream service reads with its own consumer group. Consumer lag above 10,000
messages pages the owning team.

## Retention

Events are kept for 7 days; compacted snapshots of each order are kept indefinitely.
