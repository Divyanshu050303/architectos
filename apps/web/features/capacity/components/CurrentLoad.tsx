import { formatCompact, formatNumber } from "@/lib/formatting";
import type { CapacityAnalysis } from "@/types/capacity";

import { MetricCard } from "./MetricCard";

export function CurrentLoad({ load }: { load: CapacityAnalysis["load"] }) {
  return (
    <section aria-labelledby="current-load-heading" className="flex flex-col gap-2">
      <h2 id="current-load-heading" className="label-caps">
        Current load
      </h2>
      <dl className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <MetricCard
          label="Daily active users"
          value={formatCompact(load.dailyActiveUsers)}
          unit="DAU"
          description={`${formatNumber(load.dailyActiveUsers, 0)} users`}
        />
        <MetricCard
          label="Peak requests"
          value={formatCompact(load.peakRps)}
          unit="RPS"
          description={`${formatNumber(load.peakRps, 0)} req/s at peak`}
        />
        <MetricCard
          label="Writes"
          value={formatCompact(load.writesPerSecond)}
          unit="writes/s"
          description={`${formatNumber(load.writesPerSecond, 0)} writes/s at peak`}
        />
      </dl>
    </section>
  );
}
