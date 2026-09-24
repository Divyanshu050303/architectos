import { describe, expect, it } from "vitest";

import {
  DEFAULT_DEMO_INPUT,
  nearestPeakRpsStep,
  PEAK_RPS_STEPS,
  peakRpsFromDau,
  runDemoModel,
} from "@/features/marketing/demo-model";

describe("landing demo model (spec §120, illustrative)", () => {
  it("derives peak RPS from DAU by default", () => {
    const result = runDemoModel(DEFAULT_DEMO_INPUT);
    expect(result.peakRpsOverridden).toBe(false);
    expect(result.peakRps).toBeCloseTo(peakRpsFromDau(100_000));
    expect(result.derivedPeakRps).toBe(result.peakRps);
  });

  it("runs with an overridden peak and keeps the DAU estimate as a reference", () => {
    const result = runDemoModel({ ...DEFAULT_DEMO_INPUT, peakRpsOverride: 20_000 });
    expect(result.peakRpsOverridden).toBe(true);
    expect(result.peakRps).toBe(20_000);
    expect(result.derivedPeakRps).toBeCloseTo(peakRpsFromDau(100_000));
    // 20K rps overwhelms a single primary without a cache, even at 100K DAU.
    expect(result.components.postgres.status).toBe("critical");
    expect(result.bottleneck).not.toBeNull();
  });

  it("ignores a non-positive override", () => {
    expect(runDemoModel({ ...DEFAULT_DEMO_INPUT, peakRpsOverride: 0 }).peakRpsOverridden).toBe(false);
  });

  it("snaps a rate to the nearest slider stop on a log scale", () => {
    expect(PEAK_RPS_STEPS[nearestPeakRpsStep(185)]).toBe(200);
    expect(PEAK_RPS_STEPS[nearestPeakRpsStep(18_518)]).toBe(20_000);
    expect(PEAK_RPS_STEPS[nearestPeakRpsStep(1)]).toBe(100);
    expect(PEAK_RPS_STEPS[nearestPeakRpsStep(1e9)]).toBe(50_000);
  });
});
