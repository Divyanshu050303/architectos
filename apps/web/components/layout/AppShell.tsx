"use client";

import { CommandPalette } from "@/components/command/CommandPalette";
import { GlobalCommands } from "@/components/command/GlobalCommands";
import { TopNav } from "@/components/navigation/TopNav";
import { CreateProjectDialogHost } from "@/features/projects/components/CreateProjectDialog";

/** Shell for pages outside a project (dashboard, project list). */
export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-dvh flex-col">
      <SkipLink />
      <TopNav />
      <main id="main" className="min-h-0 flex-1 overflow-y-auto">
        {children}
      </main>
      <GlobalCommands />
      <CommandPalette />
      <CreateProjectDialogHost />
    </div>
  );
}

export function SkipLink() {
  return (
    <a
      href="#main"
      className="sr-only z-50 rounded-sm bg-surface px-3 py-2 text-sm text-fg focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
    >
      Skip to content
    </a>
  );
}
