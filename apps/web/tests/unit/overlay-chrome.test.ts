import { describe, expect, it } from "vitest";

import {
  fetchingForVersion,
  isOverlayLoading,
  type OverlayChromeInput,
  overlayChrome,
  type OverlayFlags,
} from "@/features/architecture/utils/overlay-chrome";

const NONE: OverlayFlags = {
  capacity: false,
  validation: false,
  reliability: false,
  security: false,
  cost: false,
  observability: false,
  simulation: false,
};

function input(overrides: Partial<OverlayChromeInput> = {}): OverlayChromeInput {
  return {
    mode: "topology",
    page: (segment) => `/project/p/${segment}`,
    capacity: null,
    validation: null,
    reliability: null,
    security: null,
    cost: null,
    observability: null,
    run: null,
    hasSimulationResult: false,
    pending: NONE,
    ...overrides,
  };
}

describe("isOverlayLoading", () => {
  it("only looks at the analyses behind the active overlay", () => {
    expect(isOverlayLoading("cost", { ...NONE, security: true })).toBe(false);
    expect(isOverlayLoading("security", { ...NONE, security: true })).toBe(true);
    expect(isOverlayLoading("topology", { ...NONE, validation: true })).toBe(true);
    expect(isOverlayLoading("simulation", { ...NONE, simulation: true })).toBe(true);
  });
});

describe("fetchingForVersion", () => {
  const idle = { isLoading: false, isFetching: false };

  it("is true on the first load", () => {
    expect(fetchingForVersion({ isLoading: true, isFetching: true }, 3)).toBe(true);
  });

  it("is true while refetching data computed for another version", () => {
    expect(
      fetchingForVersion({ isLoading: false, isFetching: true, data: { architectureVersion: 2 } }, 3),
    ).toBe(true);
  });

  it("is false for a background refetch of the same version, or when idle", () => {
    expect(
      fetchingForVersion({ isLoading: false, isFetching: true, data: { architectureVersion: 3 } }, 3),
    ).toBe(false);
    expect(fetchingForVersion({ ...idle, data: { architectureVersion: 2 } }, 3)).toBe(false);
    expect(fetchingForVersion({ ...idle, data: null }, 3)).toBe(false);
  });
});

describe("overlayChrome", () => {
  it("hints that an analysis is missing only once it has loaded", () => {
    expect(overlayChrome(input({ mode: "capacity", pending: { ...NONE, capacity: true } })).hint).toBeNull();
    expect(overlayChrome(input({ mode: "capacity" })).hint?.href).toBe("/project/p/capacity");
  });

  it("summarises backend figures", () => {
    const security = { score: 72 } as OverlayChromeInput["security"];
    const chrome = overlayChrome(input({ mode: "security", security }));
    expect(chrome.summary).toMatchObject({ label: "Security score", value: "72/100" });
    expect(chrome.hint).toBeNull();
  });

  it("explains the simulation overlay state", () => {
    expect(overlayChrome(input({ mode: "simulation" })).hint?.action).toBe("Run a simulation");
    const running = { status: "running" } as OverlayChromeInput["run"];
    expect(overlayChrome(input({ mode: "simulation", run: running })).hint?.action).toBe("View progress");
    const done = { status: "succeeded" } as OverlayChromeInput["run"];
    expect(
      overlayChrome(input({ mode: "simulation", run: done, hasSimulationResult: true })).hint,
    ).toBeNull();
  });

  it("shows nothing for topology", () => {
    expect(overlayChrome(input())).toEqual({ hint: null, summary: null });
  });
});
