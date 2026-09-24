import type { Metadata } from "next";

import { AppShell } from "@/components/layout/AppShell";
import { DashboardView } from "@/features/projects/components/DashboardView";

export const metadata: Metadata = { title: "Dashboard" };

export default function DashboardPage() {
  return (
    <AppShell>
      <DashboardView />
    </AppShell>
  );
}
