"use client";

import { FlaskConical } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Combobox } from "@/components/ui/combobox";
import { Field, Select, type SelectOption } from "@/components/ui/input";
import type { SimulationConfig, SimulationScenario } from "@/types/simulation";

export const TRAFFIC_LABELS: Record<SimulationConfig["traffic"], string> = {
  current: "Current",
  peak: "Peak",
  "2x": "2×",
  "10x": "10×",
};

export const DURATION_OPTIONS = [1, 5, 15, 60] as const;

export const ENVIRONMENT_LABELS: Record<SimulationConfig["environment"], string> = {
  production_like: "Production-like",
  staging: "Staging",
};

/** Above this many scenarios the picker becomes searchable (spec §73 Combobox). */
export const SEARCHABLE_SCENARIO_THRESHOLD = 5;

export function durationLabel(minutes: number): string {
  return `${minutes} ${minutes === 1 ? "minute" : "minutes"}`;
}

const TRAFFIC_OPTIONS: SelectOption[] = Object.entries(TRAFFIC_LABELS).map(([value, label]) => ({
  value,
  label,
}));
const ENVIRONMENT_OPTIONS: SelectOption[] = Object.entries(ENVIRONMENT_LABELS).map(([value, label]) => ({
  value,
  label,
}));
const DURATION_SELECT_OPTIONS: SelectOption[] = DURATION_OPTIONS.map((m) => ({
  value: String(m),
  label: durationLabel(m),
}));

function isTraffic(value: string): value is SimulationConfig["traffic"] {
  return value in TRAFFIC_LABELS;
}
function isEnvironment(value: string): value is SimulationConfig["environment"] {
  return value in ENVIRONMENT_LABELS;
}

export interface SimulationFormProps {
  scenarios: readonly SimulationScenario[];
  value: SimulationConfig;
  onChange: (patch: Partial<SimulationConfig>) => void;
  onRun: () => void;
  running: boolean;
  disabled?: boolean;
}

/** Scenario · Traffic · Duration · Environment · [Run Simulation] (spec §40). Controlled, presentational. */
export function SimulationForm({
  scenarios,
  value,
  onChange,
  onRun,
  running,
  disabled,
}: SimulationFormProps) {
  const scenario = scenarios.find((s) => s.id === value.scenarioId);
  return (
    <form
      aria-label="Simulation"
      className="flex flex-col gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        onRun();
      }}
    >
      <Field label="Scenario" description={scenario?.description}>
        {({ id, describedBy }) =>
          scenarios.length > SEARCHABLE_SCENARIO_THRESHOLD ? (
            <Combobox
              id={id}
              aria-describedby={describedBy}
              value={value.scenarioId}
              options={scenarios.map((s) => ({ value: s.id, label: s.label, description: s.description }))}
              onValueChange={(scenarioId) => onChange({ scenarioId })}
              placeholder="Search scenarios"
              listLabel="Scenarios"
              emptyMessage="No scenario matches."
            />
          ) : (
            <Select
              id={id}
              aria-describedby={describedBy}
              value={value.scenarioId}
              options={scenarios.map((s) => ({ value: s.id, label: s.label }))}
              onChange={(e) => onChange({ scenarioId: e.target.value })}
            />
          )
        }
      </Field>
      <Field label="Traffic">
        {({ id }) => (
          <Select
            id={id}
            value={value.traffic}
            options={TRAFFIC_OPTIONS}
            onChange={(e) => {
              if (isTraffic(e.target.value)) onChange({ traffic: e.target.value });
            }}
          />
        )}
      </Field>
      <Field label="Duration">
        {({ id }) => (
          <Select
            id={id}
            value={String(value.durationMinutes)}
            options={DURATION_SELECT_OPTIONS}
            onChange={(e) => onChange({ durationMinutes: Number(e.target.value) })}
          />
        )}
      </Field>
      <Field label="Environment">
        {({ id }) => (
          <Select
            id={id}
            value={value.environment}
            options={ENVIRONMENT_OPTIONS}
            onChange={(e) => {
              if (isEnvironment(e.target.value)) onChange({ environment: e.target.value });
            }}
          />
        )}
      </Field>
      <Button type="submit" variant="primary" loading={running} disabled={disabled || running || !scenario}>
        {running ? null : <FlaskConical aria-hidden className="size-4" />}
        {running ? "Running simulation…" : "Run Simulation"}
      </Button>
    </form>
  );
}
