/**
 * MOCK IN-MEMORY DATABASE — DEV/TEST ONLY (NEXT_PUBLIC_API_MOCKS=true).
 *
 * Holds fixture data (not real analysis), seeded from fixtures.ts and persisted to
 * sessionStorage so a page reload keeps mock edits within the tab.
 */
import { isRecord } from "@/lib/utils";

import { createSeedState, type MockDbState } from "./fixtures";

export const MOCK_DB_STORAGE_KEY = "architectos-mock-db-v2";

let state: MockDbState | null = null;

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

function isMockDbState(value: unknown): value is MockDbState {
  return (
    isRecord(value) &&
    value.schemaVersion === 2 &&
    Array.isArray(value.projects) &&
    isRecord(value.evidence) &&
    isRecord(value.proposals) &&
    isRecord(value.jobs) &&
    isRecord(value.simulations) &&
    Array.isArray(value.connectors) &&
    isRecord(value.discoverySnapshots) &&
    isRecord(value.discoveries)
  );
}

function load(): MockDbState {
  try {
    const raw = storage()?.getItem(MOCK_DB_STORAGE_KEY);
    if (raw) {
      const parsed: unknown = JSON.parse(raw);
      if (isMockDbState(parsed)) return parsed;
    }
  } catch {
    // Corrupt or inaccessible storage: fall back to the seed.
  }
  return createSeedState();
}

export function getMockDb(): MockDbState {
  state ??= load();
  return state;
}

/** Persist after a mutation. */
export function saveMockDb(): void {
  if (!state) return;
  try {
    storage()?.setItem(MOCK_DB_STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Quota exceeded or storage disabled: keep the in-memory state only.
  }
}

/** Restore the seed data (tests, or a "reset demo data" action). */
export function resetMockDb(): void {
  state = createSeedState();
  try {
    storage()?.removeItem(MOCK_DB_STORAGE_KEY);
  } catch {
    // Ignore inaccessible storage.
  }
}
