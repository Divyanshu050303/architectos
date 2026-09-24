import {
  Activity,
  Archive,
  Boxes,
  BrainCircuit,
  CalendarClock,
  ChartLine,
  Cloud,
  Container,
  CreditCard,
  Database,
  Globe,
  HardDrive,
  Inbox,
  KeyRound,
  Leaf,
  Logs,
  type LucideIcon,
  Mail,
  MessageSquare,
  Monitor,
  Network,
  Rabbit,
  Radio,
  Router,
  Search,
  Server,
  ShipWheel,
  Smartphone,
  SquareFunction,
  Table2,
  Waypoints,
  Zap,
} from "lucide-react";

import type { AnalysisMode } from "@/types/architecture";
import type { ComponentDefinition, ComponentType } from "@/types/component";

export interface ComponentTypeMeta {
  /** Category label shown in caps on the node (spec §24). */
  category: string;
  Icon: LucideIcon;
}

/** One icon system (Lucide), subtle, per component category (spec §98–99). */
export const COMPONENT_TYPE_META: Record<ComponentType, ComponentTypeMeta> = {
  client: { category: "Client", Icon: Monitor },
  cdn: { category: "CDN", Icon: Globe },
  load_balancer: { category: "Load balancer", Icon: Network },
  gateway: { category: "Gateway", Icon: Router },
  service: { category: "Service", Icon: Server },
  worker: { category: "Worker", Icon: Boxes },
  database: { category: "Database", Icon: Database },
  cache: { category: "Cache", Icon: Zap },
  queue: { category: "Queue", Icon: Inbox },
  storage: { category: "Storage", Icon: HardDrive },
  observability: { category: "Observability", Icon: Activity },
  external: { category: "External", Icon: Cloud },
};

/**
 * Recognisable icons for well-known technologies (spec §99), still Lucide and still
 * subtle. Checked in order against the component's technology, then its name; the
 * first match wins, so specific patterns come before generic ones. Anything unknown
 * falls back to the category icon above.
 */
export const TECHNOLOGY_ICONS: readonly { pattern: RegExp; Icon: LucideIcon }[] = [
  // Data stores
  { pattern: /mongo|couch|firestore|cosmos|documentdb/, Icon: Leaf },
  { pattern: /dynamo|cassandra|scylla|bigtable|hbase/, Icon: Table2 },
  { pattern: /neo4j|neptune|graph ?db|dgraph/, Icon: Waypoints },
  { pattern: /elastic ?search|opensearch|solr|algolia|meili|typesense/, Icon: Search },
  { pattern: /influx|timescale|clickhouse|druid|snowflake|bigquery|redshift/, Icon: ChartLine },
  { pattern: /redis|memcache|valkey|elasticache|dragonfly/, Icon: Zap },
  {
    pattern: /postgres|mysql|maria ?db|aurora|sql ?server|mssql|oracle|cockroach|spanner|sqlite|\brds\b/,
    Icon: Database,
  },
  // Messaging
  { pattern: /rabbit/, Icon: Rabbit },
  { pattern: /kafka|kinesis|pulsar|event ?hubs?|redpanda/, Icon: Logs },
  { pattern: /\bnats\b|\bsns\b|pub ?sub|eventbridge|mqtt/, Icon: Radio },
  { pattern: /\bsqs\b|service ?bus|activemq|celery|sidekiq/, Icon: Inbox },
  // Storage
  { pattern: /\bs3\b|object storage|blob|\bgcs\b|cloud storage|minio|\br2\b/, Icon: Archive },
  { pattern: /\bebs\b|\befs\b|block storage|file storage|\bnfs\b|disk/, Icon: HardDrive },
  // Compute and platform
  { pattern: /kubernetes|\bk8s\b|\beks\b|\bgke\b|\baks\b|openshift/, Icon: ShipWheel },
  { pattern: /lambda|cloud functions?|azure functions?|serverless|faas/, Icon: SquareFunction },
  { pattern: /docker|\becs\b|fargate|cloud run/, Icon: Container },
  { pattern: /cron|scheduler|airflow|temporal/, Icon: CalendarClock },
  // Edge and traffic
  { pattern: /cloudfront|cloudflare|fastly|akamai|\bcdn\b/, Icon: Globe },
  { pattern: /kong|apigee|api ?gateway|apollo|graphql gateway/, Icon: Router },
  { pattern: /nginx|haproxy|envoy|traefik|\balb\b|\belb\b|\bnlb\b|load ?balancer/, Icon: Network },
  // Common external services
  { pattern: /auth0|keycloak|cognito|okta|oauth|identity|\bsso\b/, Icon: KeyRound },
  { pattern: /stripe|paypal|adyen|braintree|payment/, Icon: CreditCard },
  { pattern: /sendgrid|mailgun|postmark|\bses\b|smtp|email/, Icon: Mail },
  { pattern: /twilio|\bsms\b|push notification/, Icon: MessageSquare },
  { pattern: /openai|anthropic|\bllm\b|sagemaker|vertex ai|inference/, Icon: BrainCircuit },
  { pattern: /\bios\b|android|mobile|react native|flutter/, Icon: Smartphone },
  // Observability
  { pattern: /prometheus|grafana|datadog|new relic|opentelemetry|jaeger|sentry/, Icon: Activity },
];

type IconSource = { type: ComponentType; technology: string; name: string };

const iconCache = new Map<string, LucideIcon | null>();

function technologyIcon(text: string): LucideIcon | null {
  const key = text.trim().toLowerCase();
  if (!key) return null;
  const cached = iconCache.get(key);
  if (cached !== undefined) return cached;
  const match = TECHNOLOGY_ICONS.find(({ pattern }) => pattern.test(key))?.Icon ?? null;
  iconCache.set(key, match);
  return match;
}

/** Icon for a component: its technology's icon when recognised, else its category icon. */
export function componentIcon(node: IconSource): LucideIcon {
  return technologyIcon(node.technology) ?? technologyIcon(node.name) ?? COMPONENT_TYPE_META[node.type].Icon;
}

/** Components offered by "Add component". Configuration defaults are placeholders the user edits. */
export const COMPONENT_LIBRARY: readonly ComponentDefinition[] = [
  { type: "service", name: "Service", technology: "Container service", configuration: { replicas: 2 } },
  { type: "worker", name: "Worker", technology: "Background worker", configuration: { replicas: 2 } },
  { type: "database", name: "PostgreSQL", technology: "PostgreSQL", configuration: { replicas: 1 } },
  { type: "cache", name: "Redis", technology: "Redis", configuration: { replicas: 1 } },
  { type: "queue", name: "Kafka", technology: "Kafka", configuration: { partitions: 12 } },
  {
    type: "load_balancer",
    name: "Load Balancer",
    technology: "Application load balancer",
    configuration: {},
  },
  { type: "gateway", name: "API Gateway", technology: "API gateway", configuration: {} },
  { type: "cdn", name: "CDN", technology: "CDN", configuration: {} },
  { type: "storage", name: "Object Storage", technology: "S3", configuration: {} },
  { type: "external", name: "External API", technology: "Third-party API", configuration: {} },
];

export const NODE_WIDTH = 224;
export const NODE_HEIGHT = 112;

/** Debounce for layout autosave (spec §91: 300–800ms). */
export const LAYOUT_AUTOSAVE_MS = 600;

/** Analysis overlay names (spec §68). */
export const MODE_LABELS: Record<AnalysisMode, string> = {
  topology: "Topology",
  capacity: "Capacity",
  reliability: "Reliability",
  security: "Security",
  cost: "Cost",
  observability: "Observability",
  simulation: "Simulation",
};
