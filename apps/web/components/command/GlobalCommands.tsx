"use client";

import { useRouter } from "next/navigation";

import { ALL_PROJECT_NAV_ITEMS, isNavigable, projectHref } from "@/config/navigation";
import { openCreateProjectDialog } from "@/features/projects/create-project-store";
import { pageActionHref, type PaletteCommand, useRegisterCommands } from "@/hooks/use-command";
import { useTheme } from "@/providers/theme-provider";

/**
 * Navigation-level palette commands (spec §61). Feature surfaces register their own
 * actions (generate, validate, analyze …) while they are mounted.
 *
 * Inside a project, the headline actions are also available from every page as `fallback`
 * commands: they open the owning page with `?action=…`, which that page honours once it is ready.
 * While the owning page is mounted its own command (same id) replaces the fallback.
 */
export function GlobalCommands({ projectId }: { projectId?: string }) {
  const router = useRouter();
  const { resolved, setPreference } = useTheme();

  const commands: PaletteCommand[] = [
    {
      id: "nav.dashboard",
      label: "Go to Dashboard",
      group: "Navigation",
      keywords: ["home", "systems"],
      run: () => router.push("/dashboard"),
    },
    {
      id: "nav.projects",
      label: "Go to All projects",
      group: "Navigation",
      keywords: ["systems", "list"],
      run: () => router.push("/projects"),
    },
    {
      id: "project.create",
      label: "Create project",
      group: "Project",
      keywords: ["new", "system"],
      run: openCreateProjectDialog,
    },
    {
      id: "preferences.theme",
      label: resolved === "dark" ? "Switch to light theme" : "Switch to dark theme",
      group: "Preferences",
      keywords: ["toggle theme", "dark mode", "light mode", "appearance"],
      run: () => setPreference(resolved === "dark" ? "light" : "dark"),
    },
  ];

  if (projectId) {
    for (const item of ALL_PROJECT_NAV_ITEMS) {
      if (!isNavigable(item) || item.segment === "settings") continue;
      commands.push({
        id: `nav.project.${item.segment || "overview"}`,
        label: `Go to ${item.label}`,
        group: "Navigation",
        run: () => router.push(projectHref(projectId, item.segment)),
      });
    }
    commands.push(
      {
        id: "project.settings",
        label: "Open settings",
        group: "Project",
        keywords: ["rename", "preferences", "environment"],
        run: () => router.push(projectHref(projectId, "settings")),
      },
      {
        id: "project.export-report",
        label: "Export report",
        group: "Project",
        keywords: ["report", "download", "share"],
        run: () => router.push(projectHref(projectId, "reports")),
      },
      {
        id: "architecture.generate",
        label: "Generate architecture",
        group: "Architecture",
        keywords: ["ai", "create", "requirements"],
        fallback: true,
        run: () => router.push(pageActionHref(projectHref(projectId, "requirements"), "generate")),
      },
      {
        id: "analysis.capacity.run",
        label: "Analyze capacity",
        group: "Analysis",
        keywords: ["capacity", "utilization", "bottleneck", "envelope", "load"],
        fallback: true,
        run: () => router.push(pageActionHref(projectHref(projectId, "capacity"), "analyze")),
      },
      {
        id: "analysis.simulation.run",
        label: "Run simulation",
        group: "Analysis",
        keywords: ["failure", "chaos", "scenario"],
        fallback: true,
        run: () => router.push(projectHref(projectId, "simulation")),
      },
      {
        id: "architecture.compare-versions",
        label: "Compare versions",
        group: "Architecture",
        keywords: ["diff", "history", "versions"],
        fallback: true,
        run: () => router.push(`${projectHref(projectId, "architecture")}?compare=1`),
      },
    );
  }

  useRegisterCommands(commands);
  return null;
}
