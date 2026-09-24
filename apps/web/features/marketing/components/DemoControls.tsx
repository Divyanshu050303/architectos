"use client";

/** The load and topology controls under the landing-page ArchitectureDemo, built on the UI primitives. */
import { useId } from "react";

import { RadioGroup, type RadioOption } from "@/components/ui/radio-group";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { formatCompact } from "@/lib/formatting";

import {
  DAU_STEPS,
  type DemoDatabase,
  type DemoInput,
  type DemoResult,
  MAX_REPLICAS,
  MIN_REPLICAS,
  nearestPeakRpsStep,
  PEAK_RPS_STEPS,
} from "../demo-model";
import { dauIndex, rate } from "./demo-parts";

const DATABASE_OPTIONS: ReadonlyArray<RadioOption<DemoDatabase>> = [
  { value: "postgres", label: "Primary only" },
  { value: "postgres-replica", label: "+ Read replica" },
];

function ControlHeader({
  labelId,
  label,
  value,
}: {
  labelId: string;
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span id={labelId} className="text-xs font-medium text-fg-secondary">
        {label}
      </span>
      <span className="tabular text-sm font-semibold text-fg">{value}</span>
    </div>
  );
}

export function DemoControls({
  input,
  result,
  onChange,
}: {
  input: DemoInput;
  result: DemoResult;
  onChange: (patch: Partial<DemoInput>) => void;
}) {
  const id = useId();
  const dauLabel = formatCompact(input.dau);

  return (
    <div className="grid gap-x-6 gap-y-5 border-t border-default px-4 py-4 sm:grid-cols-2 sm:px-6 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)_minmax(0,1.2fr)_auto_minmax(0,1.2fr)]">
      <div className="flex min-w-0 flex-col gap-1.5">
        <ControlHeader labelId={`${id}-dau`} label="Daily active users" value={dauLabel} />
        <Slider
          aria-labelledby={`${id}-dau`}
          min={0}
          max={DAU_STEPS.length - 1}
          step={1}
          value={dauIndex(input.dau)}
          aria-valuetext={`${dauLabel} daily active users`}
          onValueChange={(next) => onChange({ dau: DAU_STEPS[next] ?? input.dau })}
        />
        <div aria-hidden className="tabular flex justify-between text-2xs text-muted">
          <span>100K</span>
          <span>1M</span>
          <span>10M</span>
        </div>
      </div>

      <div className="flex min-w-0 flex-col gap-1.5">
        <ControlHeader labelId={`${id}-rps`} label="Peak RPS" value={rate(result.peakRps)} />
        <Slider
          aria-labelledby={`${id}-rps`}
          min={0}
          max={PEAK_RPS_STEPS.length - 1}
          step={1}
          value={nearestPeakRpsStep(result.peakRps)}
          aria-valuetext={`${rate(result.peakRps)} requests per second${
            result.peakRpsOverridden ? ", set manually" : ", estimated from daily active users"
          }`}
          aria-describedby={`${id}-rps-note`}
          onValueChange={(next) => onChange({ peakRpsOverride: PEAK_RPS_STEPS[next] ?? null })}
        />
        <div
          id={`${id}-rps-note`}
          className="tabular flex items-center justify-between gap-2 text-2xs text-muted"
        >
          {result.peakRpsOverridden ? (
            <>
              <span>DAU estimate: {rate(result.derivedPeakRps)} rps</span>
              <button
                type="button"
                onClick={() => onChange({ peakRpsOverride: null })}
                className="rounded-sm font-medium text-accent-fg underline-offset-2 hover:underline"
              >
                Use estimate
              </button>
            </>
          ) : (
            <span>
              From DAU · {rate(result.readRps)} reads · {rate(result.writeRps)} writes
            </span>
          )}
        </div>
      </div>

      <RadioGroup
        variant="segmented"
        label="Database"
        value={input.database}
        options={DATABASE_OPTIONS}
        onValueChange={(database) => onChange({ database })}
      />

      <div className="flex min-w-0 flex-col gap-1.5">
        <span id={`${id}-cache-label`} className="text-xs font-medium text-fg-secondary">
          Redis cache
        </span>
        <div className="flex h-8 items-center gap-2 text-xs font-medium text-fg">
          <Switch
            aria-labelledby={`${id}-cache-label`}
            checked={input.cache}
            onCheckedChange={(cache) => onChange({ cache })}
          />
          <span aria-hidden>{input.cache ? "On" : "Off"}</span>
        </div>
      </div>

      <div className="flex min-w-0 flex-col gap-1.5">
        <ControlHeader labelId={`${id}-replicas`} label="API replicas" value={input.apiReplicas} />
        <Slider
          aria-labelledby={`${id}-replicas`}
          min={MIN_REPLICAS}
          max={MAX_REPLICAS}
          step={1}
          value={input.apiReplicas}
          onValueChange={(apiReplicas) => onChange({ apiReplicas })}
        />
        <div aria-hidden className="tabular flex justify-between text-2xs text-muted">
          <span>{MIN_REPLICAS}</span>
          <span>{MAX_REPLICAS}</span>
        </div>
      </div>
    </div>
  );
}
