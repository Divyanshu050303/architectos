/**
 * Project sidebar navigation (spec §21). The grouping is the product lifecycle:
 * design → analyze → operate → evolve → discover → document.
 *
 * availability "v1" = the original V1 scope, "v2" = surfaces added after it (spec §4).
 * Every surface is available: each item has a route and renders as a normal link
 * (no "Soon"/"V2" tags). `hasRoute` stays so a future unshipped item can render disabled.
 */
import {
  Activity,
  Blocks,
  BookCheck,
  Boxes,
  ClipboardList,
  DollarSign,
  FileSearch,
  FileText,
  FlaskConical,
  GitBranch,
  Gauge,
  LayoutDashboard,
  type LucideIcon,
  Route,
  ScanSearch,
  Server,
  Settings,
  ShieldHalf,
  Workflow,
} from "lucide-react";

export type NavAvailability = "v1" | "v2";

export interface ProjectNavItem {
  label: string;
  /** Path segment under /project/{id}; "" is the overview. */
  segment: string;
  icon: LucideIcon;
  availability: NavAvailability;
  hasRoute: boolean;
}

export interface ProjectNavGroup {
  label: string;
  items: readonly ProjectNavItem[];
}

export const PROJECT_NAV: readonly ProjectNavGroup[] = [
  {
    label: "Project",
    items: [{ label: "Overview", segment: "", icon: LayoutDashboard, availability: "v1", hasRoute: true }],
  },
  {
    label: "Design",
    items: [
      {
        label: "Requirements",
        segment: "requirements",
        icon: ClipboardList,
        availability: "v1",
        hasRoute: true,
      },
      { label: "Architecture", segment: "architecture", icon: Workflow, availability: "v1", hasRoute: true },
    ],
  },
  {
    label: "Analyze",
    items: [
      { label: "Capacity", segment: "capacity", icon: Gauge, availability: "v1", hasRoute: true },
      { label: "Validation", segment: "validation", icon: BookCheck, availability: "v1", hasRoute: true },
      { label: "Simulation", segment: "simulation", icon: FlaskConical, availability: "v2", hasRoute: true },
    ],
  },
  {
    label: "Operate",
    items: [
      { label: "Reliability", segment: "reliability", icon: Activity, availability: "v2", hasRoute: true },
      { label: "Security", segment: "security", icon: ShieldHalf, availability: "v2", hasRoute: true },
      {
        label: "Observability",
        segment: "observability",
        icon: ScanSearch,
        availability: "v2",
        hasRoute: true,
      },
      { label: "Cost", segment: "cost", icon: DollarSign, availability: "v2", hasRoute: true },
    ],
  },
  {
    label: "Evolve",
    items: [
      { label: "Evolution", segment: "evolution", icon: GitBranch, availability: "v2", hasRoute: true },
      { label: "Migration", segment: "migration", icon: Route, availability: "v2", hasRoute: true },
    ],
  },
  {
    label: "Discover",
    items: [
      {
        label: "Infrastructure",
        segment: "infrastructure",
        icon: Server,
        availability: "v2",
        hasRoute: true,
      },
      { label: "Drift", segment: "drift", icon: Boxes, availability: "v2", hasRoute: true },
    ],
  },
  {
    label: "Document",
    items: [
      { label: "Decisions", segment: "decisions", icon: Blocks, availability: "v1", hasRoute: true },
      { label: "Evidence", segment: "evidence", icon: FileSearch, availability: "v2", hasRoute: true },
      { label: "Reports", segment: "reports", icon: FileText, availability: "v1", hasRoute: true },
    ],
  },
];

/** Settings sits below the lifecycle groups. */
export const PROJECT_SETTINGS_NAV: ProjectNavItem = {
  label: "Settings",
  segment: "settings",
  icon: Settings,
  availability: "v1",
  hasRoute: true,
};

export const ALL_PROJECT_NAV_ITEMS: readonly ProjectNavItem[] = [
  ...PROJECT_NAV.flatMap((group) => group.items),
  PROJECT_SETTINGS_NAV,
];

export function isNavigable(item: ProjectNavItem): boolean {
  return item.hasRoute;
}

export function projectHref(projectId: string, segment = ""): string {
  const base = `/project/${encodeURIComponent(projectId)}`;
  return segment ? `${base}/${segment}` : base;
}

/** Whether `pathname` is inside the item's section (overview matches only exactly). */
export function isNavItemActive(pathname: string, projectId: string, item: ProjectNavItem): boolean {
  const href = projectHref(projectId, item.segment);
  if (item.segment === "") return pathname === href || pathname === `${href}/`;
  return pathname === href || pathname.startsWith(`${href}/`);
}
