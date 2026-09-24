"use client";

/**
 * Interactive miniature architecture for the landing page (spec §120). Non-editable:
 * the controls change the load and the topology, the real ArchitectureNodeCard shows
 * the result. Numbers come from the illustrative model in ../demo-model.ts.
 */
import { Info, RotateCcw } from "lucide-react";
import { useState } from "react";

import { StatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ArchitectureNodeCard } from "@/features/architecture/components/ArchitectureNode";
import { cn } from "@/lib/utils";

import { DEFAULT_DEMO_INPUT, type DemoInput, explainDemo, runDemoModel } from "../demo-model";
import { marketingNode } from "../node-data";
import { DemoControls } from "./DemoControls";
import { Connector, nodeState, OffPlaceholder, rate } from "./demo-parts";

export function ArchitectureDemo({ className }: { className?: string }) {
  const [input, setInput] = useState<DemoInput>(DEFAULT_DEMO_INPUT);
  const result = runDemoModel(input);
  const { components: c, bottleneck } = result;
  const update = (patch: Partial<DemoInput>) => setInput((prev) => ({ ...prev, ...patch }));

  const nodes = {
    client: marketingNode({
      id: "client",
      type: "client",
      name: "Clients",
      technology: "Web + mobile",
      status: "default",
      statusLabel: "Source",
      metric: { label: "peak", value: `${rate(result.peakRps)} rps` },
      capacity: true,
    }),
    lb: marketingNode({
      id: "lb",
      type: "load_balancer",
      name: "Load balancer",
      technology: "L7 load balancer",
      capacity: true,
      ...nodeState(c.lb, bottleneck, "throughput"),
    }),
    api: marketingNode({
      id: "api",
      type: "service",
      name: "API",
      technology: "Container service",
      description: `×${input.apiReplicas}`,
      capacity: true,
      ...nodeState(c.api, bottleneck, "CPU"),
    }),
    postgres: marketingNode({
      id: "postgres",
      type: "database",
      name: "PostgreSQL",
      technology: "Primary",
      capacity: true,
      ...nodeState(c.postgres, bottleneck, "queries"),
    }),
    redis: marketingNode({
      id: "redis",
      type: "cache",
      name: "Redis",
      technology: "Cache-aside",
      capacity: true,
      ...nodeState(c.redis, bottleneck, "ops"),
    }),
    replica: marketingNode({
      id: "replica",
      type: "database",
      name: "Read replica",
      technology: "PostgreSQL",
      capacity: true,
      ...nodeState(c.replica, bottleneck, "queries"),
    }),
  };

  const dbQps = c.postgres.load + c.replica.load;

  return (
    <div
      className={cn("overflow-hidden rounded-lg border border-default bg-surface shadow-subtle", className)}
    >
      <div className="flex flex-col gap-2 border-b border-default px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-center gap-2">
          <StatusBadge
            status={result.overall}
            label={result.overall === "critical" ? "Over capacity" : undefined}
          />
          <p aria-live="polite" className="min-w-0 text-sm text-fg">
            {explainDemo(input, result)}
          </p>
        </div>
        <span className="inline-flex shrink-0 items-center gap-1.5 self-start rounded-sm border border-default bg-surface-2 px-1.5 py-0.5 text-2xs font-medium text-fg-secondary sm:self-auto">
          <Info aria-hidden className="size-3" />
          Illustrative model
        </span>
      </div>

      <figure className="m-0 bg-background px-4 py-5 sm:px-6">
        <figcaption className="sr-only">
          Clients call a load balancer, which routes to the API, which reads and writes PostgreSQL
          {input.cache ? " through a Redis cache" : ""}
          {input.database === "postgres-replica" ? " with a read replica" : ""}.
        </figcaption>
        <div
          className={cn(
            "grid grid-cols-1 items-center",
            "lg:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)_auto_minmax(0,1fr)_auto_minmax(0,1fr)]",
            "[&_[data-status]]:w-full",
          )}
        >
          <div className="lg:col-start-1 lg:row-start-1">
            <ArchitectureNodeCard data={nodes.client} />
          </div>
          <Connector flow label={`${rate(result.peakRps)} rps`} className="lg:col-start-2 lg:row-start-1" />
          <div className="lg:col-start-3 lg:row-start-1">
            <ArchitectureNodeCard data={nodes.lb} />
          </div>
          <Connector flow label={`${rate(result.peakRps)} rps`} className="lg:col-start-4 lg:row-start-1" />
          <div className="lg:col-start-5 lg:row-start-1">
            <ArchitectureNodeCard data={nodes.api} />
          </div>
          <Connector flow label={`${rate(dbQps)} qps`} className="lg:col-start-6 lg:row-start-1" />
          <div className="lg:col-start-7 lg:row-start-1">
            <ArchitectureNodeCard data={nodes.postgres} />
          </div>

          <p className="label-caps mt-4 mb-1 lg:hidden">Supporting components</p>
          <Connector
            flow={false}
            off={!input.cache}
            label={input.cache ? `${rate(c.redis.load)} ops · from API` : "cache off"}
            className="lg:col-start-5 lg:row-start-2 lg:pt-1"
          />
          <div className="lg:col-start-5 lg:row-start-3">
            {input.cache ? (
              <ArchitectureNodeCard data={nodes.redis} />
            ) : (
              <OffPlaceholder category="Cache" name="Redis" hint="Turn on the Redis cache to add it" />
            )}
          </div>
          <Connector
            flow={false}
            off={input.database !== "postgres-replica"}
            label={input.database === "postgres-replica" ? "replication" : "no replica"}
            className="mt-2 lg:col-start-7 lg:row-start-2 lg:mt-0 lg:pt-1"
          />
          <div className="lg:col-start-7 lg:row-start-3">
            {input.database === "postgres-replica" ? (
              <ArchitectureNodeCard data={nodes.replica} />
            ) : (
              <OffPlaceholder
                category="Database"
                name="Read replica"
                hint="Choose “+ Read replica” to add it"
              />
            )}
          </div>
        </div>
      </figure>

      <DemoControls input={input} result={result} onChange={update} />

      <div className="flex flex-col gap-2 border-t border-default bg-surface-2 px-4 py-2.5 text-xs text-fg-secondary sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <p>
          Illustrative model: 40 requests per user per day, 4× peak (unless you set Peak RPS), 90% reads. Real
          numbers come from the capacity engine and your requirements.
        </p>
        <Button
          size="sm"
          variant="ghost"
          className="self-start sm:self-auto"
          onClick={() => setInput(DEFAULT_DEMO_INPUT)}
          disabled={
            input.dau === DEFAULT_DEMO_INPUT.dau &&
            input.cache === DEFAULT_DEMO_INPUT.cache &&
            input.database === DEFAULT_DEMO_INPUT.database &&
            input.apiReplicas === DEFAULT_DEMO_INPUT.apiReplicas &&
            input.peakRpsOverride === DEFAULT_DEMO_INPUT.peakRpsOverride
          }
        >
          <RotateCcw aria-hidden className="size-3.5" />
          Reset
        </Button>
      </div>
    </div>
  );
}
