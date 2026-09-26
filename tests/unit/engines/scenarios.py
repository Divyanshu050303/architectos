"""Realistic requirement descriptions, reused across the engine's tests (and the evaluation set)."""

FOOD_DELIVERY = (
    "Our food delivery platform should support 100K daily active users, 2K RPS and 500 orders/sec at peak. "
    "The API p95 latency must be below 300ms and we need 99.9% availability. RPO under 5 minutes and "
    "restore within 1 hour. We use PostgreSQL, take card payments (PCI compliant), send push "
    "notifications and offer real-time tracking. Encrypt PII at rest and retain orders for 7 years. "
    "Keep infrastructure costs under $20,000 per month."
)
ECOMMERCE = (
    "An e-commerce store for 2M monthly active users with at least 3,000 requests per second during "
    "sales. Checkout p99 latency under 500 ms. 99.95% uptime. Payments are PCI DSS compliant and orders "
    "need strong consistency. Encrypt data in transit. Retain orders for 10 years. Deploy to us-east-1 "
    "and eu-west-1."
)
SAAS_DASHBOARD = (
    "A multi-tenant SaaS analytics dashboard for B2B customers. 5,000 concurrent users, p95 latency "
    "under 800 ms for dashboards, 99.9% availability. SSO with MFA and role-based access for each "
    "tenant. Budget: at most 8,000 EUR/month."
)
FINTECH = (
    "A payment platform moving money between bank accounts. At least 1,000 transactions per second is "
    "expected - roughly 1000 requests/sec. 99.99% availability, RPO of 0 minutes and RTO under 15 "
    "minutes. Ledger entries need strong consistency and must be retained for 7 years. PCI DSS and "
    "GDPR apply; encrypt everything at rest and in transit."
)
SOCIAL = (
    "A social platform for 10M monthly active users. Feed p95 latency under 200 ms, at least 20k rps "
    "at peak, 99.9% availability. Store 500 TB of media. Global users."
)
IOT_TELEMETRY = (
    "An IoT telemetry service ingesting from 2M devices: at least 50,000 requests per second, "
    "ingesting 2 TB per day. Retain raw data for 90 days. RPO under 1 minute."
)
INTERNAL_ADMIN = "An internal admin tool for our support staff. Staff sign in with SSO and role-based access."

ALL = {
    "food_delivery": FOOD_DELIVERY,
    "ecommerce": ECOMMERCE,
    "saas_dashboard": SAAS_DASHBOARD,
    "fintech": FINTECH,
    "social": SOCIAL,
    "iot_telemetry": IOT_TELEMETRY,
    "internal_admin": INTERNAL_ADMIN,
}
