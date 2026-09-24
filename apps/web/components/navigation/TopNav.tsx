"use client";

import { LogOut, Menu, Search, Share2, Upload, UserRound } from "lucide-react";
import Link from "next/link";
import { Popover as RadixPopover } from "radix-ui";
import { useState } from "react";

import { LogoMark, Wordmark } from "@/components/brand/Logo";
import { COMMAND_PALETTE_SHORTCUT } from "@/components/command/CommandPalette";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { IconButton } from "@/components/ui/icon-button";
import { Input } from "@/components/ui/input";
import { Kbd } from "@/components/ui/kbd";
import { Popover, PopoverContent } from "@/components/ui/popover";
import { toast } from "@/components/ui/toast";
import { Tooltip } from "@/components/ui/tooltip";
import { config } from "@/config/env";
import { projectHref } from "@/config/navigation";
import { useSession, useSignOut } from "@/hooks/use-auth";
import { useProject } from "@/hooks/use-projects";
import { useUiStore } from "@/stores/ui-store";

import { ThemeToggle } from "./ThemeToggle";

/**
 * Share (spec §20, §52): copies the current deep link — the workspace keeps ?node, ?mode,
 * ?view and friends in the URL — to the clipboard. Where the clipboard is unavailable the
 * link is shown in a popover to copy by hand.
 */
export function ShareButton() {
  const [fallbackUrl, setFallbackUrl] = useState<string | null>(null);

  async function share() {
    const url = window.location.href;
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(url);
      toast("Link copied", { description: "Anyone with access sees this exact view.", tone: "success" });
    } catch {
      setFallbackUrl(url);
    }
  }

  return (
    <Popover open={fallbackUrl !== null} onOpenChange={(open) => !open && setFallbackUrl(null)}>
      <RadixPopover.Anchor asChild>
        <Button
          variant="ghost"
          size="sm"
          aria-label="Share: copy link to this view"
          onClick={() => void share()}
          className="px-2 sm:px-2.5"
        >
          <Share2 aria-hidden className="size-3.5" />
          <span className="hidden md:inline">Share</span>
        </Button>
      </RadixPopover.Anchor>
      <PopoverContent align="end" className="flex w-80 max-w-[calc(100vw-1rem)] flex-col gap-2">
        <p className="text-xs text-fg-secondary">Copy this link to share the current view.</p>
        <Input
          readOnly
          autoFocus
          aria-label="Link to this view"
          value={fallbackUrl ?? ""}
          onFocus={(event) => event.currentTarget.select()}
          className="h-8 text-xs"
        />
      </PopoverContent>
    </Popover>
  );
}

/** Compact top bar (spec §20): not a traditional SaaS navbar. */
export function TopNav({ projectId }: { projectId?: string }) {
  const openPalette = useUiStore((s) => s.setCommandPaletteOpen);
  const setMobileSidebarOpen = useUiStore((s) => s.setMobileSidebarOpen);

  return (
    <header className="flex h-12 min-w-0 shrink-0 items-center gap-2 border-b border-default bg-surface px-3">
      {projectId ? (
        <IconButton label="Open navigation" className="lg:hidden" onClick={() => setMobileSidebarOpen(true)}>
          <Menu aria-hidden />
        </IconButton>
      ) : null}

      <Link
        href="/dashboard"
        aria-label="ArchitectOS"
        className="flex shrink-0 items-center gap-2 rounded-sm px-1 text-sm font-semibold tracking-tight text-fg"
      >
        {projectId ? (
          <>
            {/* The mark alone below sm keeps the project crumb readable at phone widths. */}
            <span className="hidden sm:inline-flex">
              <Wordmark />
            </span>
            <span className="inline-flex sm:hidden">
              <LogoMark size={20} />
            </span>
          </>
        ) : (
          <Wordmark />
        )}
      </Link>

      {projectId ? <ProjectCrumb projectId={projectId} /> : null}

      <div className="ml-auto flex shrink-0 items-center gap-0.5 sm:gap-1">
        {/* Responsive visibility sits on wrappers: the components' own inline-flex would
            otherwise win over `hidden` (same property, later in the stylesheet). */}
        {config.useMocks ? (
          <span className="mr-1 inline-flex">
            {/* Always visible so mock responses never pass for real ones; compact on phones. */}
            <Tooltip content="Responses come from the in-browser mock backend, not the ArchitectOS API. Figures are illustrative.">
              <Badge tone="info" tabIndex={0}>
                Mock<span className="sr-only sm:not-sr-only">&nbsp;data</span>
              </Badge>
            </Tooltip>
          </span>
        ) : null}

        <span className="hidden sm:inline-flex">
          <Button
            variant="ghost"
            size="sm"
            aria-keyshortcuts="Meta+K Control+K"
            onClick={() => openPalette(true)}
            className="text-muted"
          >
            <Search aria-hidden className="size-3.5" />
            Search
            <Kbd shortcut={COMMAND_PALETTE_SHORTCUT} />
          </Button>
        </span>
        <IconButton
          label="Open command palette"
          shortcut={COMMAND_PALETTE_SHORTCUT}
          className="sm:hidden"
          onClick={() => openPalette(true)}
        >
          <Search aria-hidden />
        </IconButton>

        {projectId ? (
          <>
            <ShareButton />
            <span className="hidden md:inline-flex">
              <Button asChild variant="ghost" size="sm">
                <Link href={projectHref(projectId, "reports")}>
                  <Upload aria-hidden className="size-3.5" />
                  Export
                </Link>
              </Button>
            </span>
          </>
        ) : null}

        <ThemeToggle />
        <UserMenu />
      </div>
    </header>
  );
}

function ProjectCrumb({ projectId }: { projectId: string }) {
  const { data: project, isPending } = useProject(projectId);
  return (
    <div className="flex min-w-0 items-center gap-2">
      <span aria-hidden className="text-muted">
        /
      </span>
      {isPending ? (
        <span className="h-4 w-28 animate-pulse rounded-sm bg-surface-2" aria-label="Loading project" />
      ) : (
        <Link
          href={projectHref(projectId)}
          className="truncate rounded-sm text-sm font-medium text-fg hover:text-fg-secondary"
        >
          {project?.name ?? "Unknown project"}
        </Link>
      )}
      {project?.architectureVersion ? (
        <Link
          href={projectHref(projectId, "architecture")}
          aria-label={`Architecture version ${project.architectureVersion}`}
          className="shrink-0"
        >
          <Badge tone="neutral" className="tabular">
            v{project.architectureVersion}
          </Badge>
        </Link>
      ) : null}
    </div>
  );
}

function UserMenu() {
  const { data: user } = useSession();
  const signOut = useSignOut();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <IconButton label="Account">
          <UserRound aria-hidden />
        </IconButton>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-60">
        <DropdownMenuLabel>Account</DropdownMenuLabel>
        {user ? (
          <div className="flex flex-col px-2 pb-2">
            <span className="truncate text-sm font-medium text-fg">{user.name}</span>
            <span className="truncate text-xs text-fg-secondary">{user.email}</span>
          </div>
        ) : null}
        <DropdownMenuItem disabled={signOut.isPending} onSelect={() => signOut.mutate()}>
          <LogOut aria-hidden />
          Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
