"use client";

import { CommandPalette } from "@/components/command/CommandPalette";
import { GlobalCommands } from "@/components/command/GlobalCommands";
import { Sidebar } from "@/components/navigation/Sidebar";
import { TopNav } from "@/components/navigation/TopNav";
import { EvidenceDrawer } from "@/features/evidence/components/EvidenceDrawer";
import { CreateProjectDialogHost } from "@/features/projects/components/CreateProjectDialog";

import { SkipLink } from "./AppShell";

/**
 * Project workspace shell (spec §19): top navigation, grouped sidebar and a
 * full-height main region. Pages fill `main`; the architecture page lays out its own
 * canvas, inspector and command bar inside it.
 */
export function ProjectShell({ projectId, children }: { projectId: string; children: React.ReactNode }) {
  return (
    <div className="flex h-dvh flex-col print:h-auto">
      <SkipLink />
      <div className="contents print:hidden">
        <TopNav projectId={projectId} />
      </div>
      <div className="flex min-h-0 flex-1">
        <div className="contents print:hidden">
          <Sidebar projectId={projectId} />
        </div>
        <main
          id="main"
          className="relative flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto print:overflow-visible"
        >
          {children}
        </main>
      </div>
      <GlobalCommands projectId={projectId} />
      <CommandPalette />
      <CreateProjectDialogHost />
      <EvidenceDrawer />
    </div>
  );
}
