/** Migration plans between evolution stages (spec §42). */
import { useQuery } from "@tanstack/react-query";

import { getMigration, listMigrations } from "@/api/migrations";
import { queryKeys } from "@/lib/query-keys";

export function useMigrations(projectId: string) {
  return useQuery({
    queryKey: queryKeys.migrations(projectId),
    queryFn: ({ signal }) => listMigrations(projectId, signal),
    enabled: Boolean(projectId),
  });
}

export function useMigration(migrationId: string | null) {
  return useQuery({
    queryKey: queryKeys.migration(migrationId ?? ""),
    queryFn: ({ signal }) => getMigration(migrationId ?? "", signal),
    enabled: migrationId !== null,
  });
}
