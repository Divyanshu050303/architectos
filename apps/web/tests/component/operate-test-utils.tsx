import { createSeedState } from "@/api/mock/fixtures";
import type { Architecture } from "@/types/architecture";

/** The current Food Delivery architecture (v3) from the mock seed. */
export function foodArchitecture(): Architecture {
  const food = createSeedState().projects.find((p) => p.id === "proj_food");
  const current = food?.versions[food.versions.length - 1];
  if (!current) throw new Error("Food Delivery fixture missing");
  return current;
}

export function queryResult<T>(data: T) {
  return { data, isPending: false, isError: false, error: null, refetch: () => Promise.resolve() };
}

export function mutationResult() {
  return { mutate: () => {}, isPending: false };
}
