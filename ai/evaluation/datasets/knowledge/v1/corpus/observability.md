# Observability

## Metrics

Every service exports request rate, error rate and latency histograms.

## Alerts

An alert fires when the error rate stays above 1% for 5 minutes, or when p95
latency exceeds 500 ms for 10 minutes.

## Tracing

Distributed tracing uses OpenTelemetry; traces are sampled at 10% and kept for 3 days.

## Dashboards

Each team owns a dashboard per service, reviewed in the weekly operations meeting.
