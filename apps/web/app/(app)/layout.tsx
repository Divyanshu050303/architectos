import type { Metadata } from "next";

import { RequireAuth } from "@/components/auth/RequireAuth";

/**
 * The authenticated application (spec §117), grouped apart from the marketing pages in
 * `app/(marketing)/`. The group adds no URL segment: routes stay /app, /dashboard, /projects and
 * /project/[projectId]/…; /app is the entry point and redirects to /dashboard.
 *
 * Shells (AppShell / ProjectShell) stay per route because the dashboard and project pages use
 * different chrome; this layout only carries what every application route shares: robots
 * rules and the sign-in gate.
 */
export const metadata: Metadata = {
  // Workspace pages hold user data and are never meant for search engines.
  robots: { index: false, follow: false },
};

export default function ApplicationLayout({ children }: LayoutProps<"/">) {
  return <RequireAuth>{children}</RequireAuth>;
}
