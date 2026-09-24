"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { DrawerContent, Drawer } from "@/components/ui/drawer";
import {
  isNavigable,
  isNavItemActive,
  PROJECT_NAV,
  PROJECT_SETTINGS_NAV,
  projectHref,
  type ProjectNavItem,
} from "@/config/navigation";
import { cn } from "@/lib/utils";
import { useUiStore } from "@/stores/ui-store";

/** Grouped project navigation (spec §21). A drawer below the lg breakpoint. */
export function Sidebar({ projectId }: { projectId: string }) {
  const open = useUiStore((s) => s.mobileSidebarOpen);
  const setOpen = useUiStore((s) => s.setMobileSidebarOpen);
  return (
    <>
      <aside className="hidden w-56 shrink-0 flex-col overflow-y-auto border-r border-default bg-surface lg:flex">
        <SidebarNav projectId={projectId} />
      </aside>
      <Drawer open={open} onOpenChange={setOpen}>
        <DrawerContent
          title="Navigation"
          className="right-auto! left-0! max-w-72! border-r! border-l-0! lg:hidden"
        >
          <div className="-mx-5 -my-4">
            <SidebarNav projectId={projectId} onNavigate={() => setOpen(false)} />
          </div>
        </DrawerContent>
      </Drawer>
    </>
  );
}

export function SidebarNav({ projectId, onNavigate }: { projectId: string; onNavigate?: () => void }) {
  const pathname = usePathname() ?? "";
  return (
    <nav aria-label="Project" className="flex flex-1 flex-col gap-4 px-2 py-3">
      {PROJECT_NAV.map((group) => {
        const headingId = `nav-group-${group.label.toLowerCase()}`;
        return (
          <div key={group.label} className="flex flex-col gap-0.5">
            <h2 id={headingId} className="label-caps px-2 pb-1">
              {group.label}
            </h2>
            <ul aria-labelledby={headingId} className="flex flex-col gap-0.5">
              {group.items.map((item) => (
                <li key={item.segment || "overview"}>
                  <NavItem
                    item={item}
                    projectId={projectId}
                    active={isNavItemActive(pathname, projectId, item)}
                    onNavigate={onNavigate}
                  />
                </li>
              ))}
            </ul>
          </div>
        );
      })}
      <div className="mt-auto border-t border-default pt-3">
        <NavItem
          item={PROJECT_SETTINGS_NAV}
          projectId={projectId}
          active={isNavItemActive(pathname, projectId, PROJECT_SETTINGS_NAV)}
          onNavigate={onNavigate}
        />
      </div>
    </nav>
  );
}

const ITEM = "relative flex h-8 items-center gap-2 rounded-sm px-2 text-sm [&_svg]:size-4 [&_svg]:shrink-0";

function NavItem({
  item,
  projectId,
  active,
  onNavigate,
}: {
  item: ProjectNavItem;
  projectId: string;
  active: boolean;
  onNavigate?: () => void;
}) {
  const Icon = item.icon;
  if (!isNavigable(item)) {
    return (
      <span
        role="link"
        aria-disabled="true"
        title={`${item.label} is planned for a later release`}
        className={cn(ITEM, "cursor-not-allowed text-muted opacity-70")}
      >
        <Icon aria-hidden />
        <span className="flex-1 truncate">{item.label}</span>
        <span className="rounded-sm border border-default px-1 text-2xs text-muted">Soon</span>
      </span>
    );
  }
  return (
    <Link
      href={projectHref(projectId, item.segment)}
      aria-current={active ? "page" : undefined}
      onClick={onNavigate}
      className={cn(
        ITEM,
        "transition-colors",
        active
          ? "bg-accent-soft font-medium text-accent-fg"
          : "text-fg-secondary hover:bg-surface-2 hover:text-fg",
      )}
    >
      {active ? (
        <span aria-hidden className="absolute inset-y-1.5 -left-2 w-0.5 rounded-full bg-accent-strong" />
      ) : null}
      <Icon aria-hidden />
      <span className="flex-1 truncate">{item.label}</span>
    </Link>
  );
}
