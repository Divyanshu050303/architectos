/**
 * Landing-page demo model (spec §120).
 *
 * ILLUSTRATIVE ONLY — real numbers come from the capacity engine. This tiny formula
 * exists so the marketing demo can react to its controls without a backend. It is not
 * used anywhere in the application, and the UI labels it "Illustrative model".
 *
 *   peakRps      = DAU × 40 requests/day ÷ 86 400 s × 4 (peak-to-average),
 *                  unless the visitor overrides it with the Peak RPS control
 *   reads        = 90% of requests, writes = 10%
 *   cache on     → 85% of reads served by Redis
 *   read replica → remaining reads split evenly between primary and replica
 *   utilisation  = demand ÷ per-component capacity (below)
 *   warning ≥ 70%, critical ≥ 100%
 */

export const DAU_STEPS = [100_000, 200_000, 500_000, 1_000_000, 2_000_000, 5_000_000, 10_000_000] as const;

/** Stops for the Peak RPS override slider. */
export const PEAK_RPS_STEPS = [100, 200, 500, 1_000, 2_000, 5_000, 10_000, 20_000, 50_000] as const;

export const MIN_REPLICAS = 1;
export const MAX_REPLICAS = 6;

export type DemoDatabase = "postgres" | "postgres-replica";

export interface DemoInput {
  dau: number;
  database: DemoDatabase;
  cache: boolean;
  apiReplicas: number;
  /** Peak requests per second set directly; null derives it from DAU. */
  peakRpsOverride: number | null;
}

export const DEFAULT_DEMO_INPUT: DemoInput = {
  dau: 100_000,
  database: "postgres",
  cache: false,
  apiReplicas: 3,
  peakRpsOverride: null,
};

/** Illustrative per-component capacities. */
export const DEMO_CAPACITY = {
  loadBalancerRps: 50_000,
  apiRpsPerReplica: 1_200,
  postgresQps: 2_500,
  redisOps: 100_000,
} as const;

const REQUESTS_PER_USER_PER_DAY = 40;
const PEAK_FACTOR = 4;
const READ_RATIO = 0.9;
const CACHE_HIT_RATE = 0.85;

export const WARNING_THRESHOLD = 0.7;
export const CRITICAL_THRESHOLD = 1;

export type DemoStatus = "healthy" | "warning" | "critical";
export type DemoComponentId = "lb" | "api" | "postgres" | "redis" | "replica";

export interface DemoComponent {
  id: DemoComponentId;
  /** false when the component is switched off by the controls. */
  enabled: boolean;
  /** Demand ÷ capacity; 0 when disabled. */
  utilization: number;
  /** Operations per second reaching the component. */
  load: number;
  status: DemoStatus;
}

export interface DemoResult {
  /** The peak the model ran with: the override when set, otherwise derived from DAU. */
  peakRps: number;
  /** Always the DAU-derived peak, shown as a reference while overridden. */
  derivedPeakRps: number;
  peakRpsOverridden: boolean;
  readRps: number;
  writeRps: number;
  components: Record<DemoComponentId, DemoComponent>;
  /** Most utilised enabled component when it crosses the warning threshold. */
  bottleneck: DemoComponentId | null;
  overall: DemoStatus;
}

export function statusFor(utilization: number): DemoStatus {
  if (utilization >= CRITICAL_THRESHOLD) return "critical";
  if (utilization >= WARNING_THRESHOLD) return "warning";
  return "healthy";
}

function component(id: DemoComponentId, load: number, capacity: number, enabled = true): DemoComponent {
  const utilization = enabled ? load / capacity : 0;
  return { id, enabled, load: enabled ? load : 0, utilization, status: statusFor(utilization) };
}

export function peakRpsFromDau(dau: number): number {
  return ((dau * REQUESTS_PER_USER_PER_DAY) / 86_400) * PEAK_FACTOR;
}

/** Index of the Peak RPS stop closest to `rps` (log distance). */
export function nearestPeakRpsStep(rps: number): number {
  let best = 0;
  PEAK_RPS_STEPS.forEach((step, index) => {
    const current = PEAK_RPS_STEPS[best] ?? step;
    if (Math.abs(Math.log(step / rps)) < Math.abs(Math.log(current / rps))) best = index;
  });
  return best;
}

export function runDemoModel(input: DemoInput): DemoResult {
  const replicas = Math.min(Math.max(Math.round(input.apiReplicas), MIN_REPLICAS), MAX_REPLICAS);
  const derivedPeakRps = peakRpsFromDau(input.dau);
  const peakRpsOverridden = input.peakRpsOverride !== null && input.peakRpsOverride > 0;
  const peakRps = peakRpsOverridden ? (input.peakRpsOverride ?? derivedPeakRps) : derivedPeakRps;
  const readRps = peakRps * READ_RATIO;
  const writeRps = peakRps - readRps;

  const cacheReads = input.cache ? readRps * CACHE_HIT_RATE : 0;
  const dbReads = readRps - cacheReads;
  const hasReplica = input.database === "postgres-replica";
  const primaryReads = hasReplica ? dbReads / 2 : dbReads;

  const components: Record<DemoComponentId, DemoComponent> = {
    lb: component("lb", peakRps, DEMO_CAPACITY.loadBalancerRps),
    api: component("api", peakRps, DEMO_CAPACITY.apiRpsPerReplica * replicas),
    postgres: component("postgres", writeRps + primaryReads, DEMO_CAPACITY.postgresQps),
    redis: component("redis", readRps, DEMO_CAPACITY.redisOps, input.cache),
    replica: component("replica", dbReads / 2, DEMO_CAPACITY.postgresQps, hasReplica),
  };

  let bottleneck: DemoComponentId | null = null;
  for (const c of Object.values(components)) {
    if (!c.enabled || c.utilization < WARNING_THRESHOLD) continue;
    if (!bottleneck || c.utilization > components[bottleneck].utilization) bottleneck = c.id;
  }

  return {
    peakRps,
    derivedPeakRps,
    peakRpsOverridden,
    readRps,
    writeRps,
    components,
    bottleneck,
    overall: bottleneck ? components[bottleneck].status : "healthy",
  };
}

/** One-line, deterministic explanation of the current state of the demo. */
export function explainDemo(input: DemoInput, result: DemoResult): string {
  const { bottleneck } = result;
  if (!bottleneck) return "All components are below the 70% warning threshold.";
  const pct = Math.round(result.components[bottleneck].utilization * 100);
  switch (bottleneck) {
    case "postgres": {
      if (!input.cache) return `PostgreSQL primary at ${pct}%. Enable the Redis cache to absorb reads.`;
      if (input.database === "postgres")
        return `PostgreSQL primary at ${pct}%. Add a read replica to split the remaining reads.`;
      return `PostgreSQL primary at ${pct}%: writes alone exceed one primary. Partition or queue writes.`;
    }
    case "replica":
      return `Read replica at ${pct}%. Enable the cache or add replicas.`;
    case "api":
      return input.apiReplicas < MAX_REPLICAS
        ? `API at ${pct}% across ${input.apiReplicas} replicas. Add replicas.`
        : `API at ${pct}% even at ${MAX_REPLICAS} replicas. Autoscale beyond this range.`;
    case "lb":
      return `Load balancer at ${pct}%. Shard traffic across load balancers.`;
    case "redis":
      return `Redis at ${pct}%. Cluster the cache.`;
  }
}
