"use client";

import { Search } from "lucide-react";
import { Dialog as RadixDialog } from "radix-ui";
import { useEffect, useId, useMemo, useRef, useState } from "react";

import { useRestoreFocus } from "@/components/ui/dialog";
import { Kbd } from "@/components/ui/kbd";
import {
  DEFAULT_DISABLED_REASON,
  PALETTE_GROUP_ORDER,
  type PaletteCommand,
  usePaletteCommands,
} from "@/hooks/use-command";
import { matchesShortcut } from "@/lib/keyboard";
import { cn } from "@/lib/utils";
import { useUiStore } from "@/stores/ui-store";

export const COMMAND_PALETTE_SHORTCUT = "mod+k";

/** Every whitespace-separated term must appear in the label, group or keywords. */
export function filterCommands(commands: readonly PaletteCommand[], query: string): PaletteCommand[] {
  const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
  const matched = terms.length
    ? commands.filter((command) => {
        const haystack = [command.label, command.group, ...(command.keywords ?? [])].join(" ").toLowerCase();
        return terms.every((term) => haystack.includes(term));
      })
    : [...commands];
  // Stable group order; registration order within a group.
  return matched.sort((a, b) => PALETTE_GROUP_ORDER.indexOf(a.group) - PALETTE_GROUP_ORDER.indexOf(b.group));
}

/** Global ⌘K / Ctrl+K. A modifier shortcut, so it also works while typing. */
function useCommandPaletteShortcut() {
  const toggle = useUiStore((s) => s.toggleCommandPalette);
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.defaultPrevented || !matchesShortcut(event, COMMAND_PALETTE_SHORTCUT)) return;
      event.preventDefault();
      toggle();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [toggle]);
}

export function CommandPalette() {
  useCommandPaletteShortcut();
  const open = useUiStore((s) => s.commandPaletteOpen);
  const setOpen = useUiStore((s) => s.setCommandPaletteOpen);
  // Opened by a shortcut, not a trigger: give focus back to where it was on close.
  const focus = useRestoreFocus({});
  return (
    <RadixDialog.Root open={open} onOpenChange={setOpen}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="fixed inset-0 z-40 bg-overlay" />
        <RadixDialog.Content
          aria-describedby={undefined}
          {...focus}
          className={cn(
            "fixed top-[12vh] left-1/2 z-50 flex max-h-[70vh] w-[calc(100vw-2rem)] max-w-xl -translate-x-1/2",
            "flex-col overflow-hidden rounded-lg border border-default bg-surface shadow-raised focus-visible:outline-none",
          )}
        >
          <RadixDialog.Title className="sr-only">Command palette</RadixDialog.Title>
          {/* Mounted only while open, so every opening starts with an empty query. */}
          <PaletteBody onClose={() => setOpen(false)} />
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}

function PaletteBody({ onClose }: { onClose: () => void }) {
  const commands = usePaletteCommands();
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);
  const baseId = useId();
  const listboxId = `${baseId}-listbox`;
  const optionId = (index: number) => `${baseId}-option-${index}`;

  const results = useMemo(() => filterCommands(commands, query), [commands, query]);
  const safeIndex = results.length ? Math.min(activeIndex, results.length - 1) : -1;
  const active = safeIndex >= 0 ? results[safeIndex] : undefined;

  useEffect(() => {
    if (safeIndex < 0) return;
    const node = Array.from(listRef.current?.querySelectorAll<HTMLElement>('[role="option"]') ?? [])[
      safeIndex
    ];
    node?.scrollIntoView?.({ block: "nearest" });
  }, [safeIndex]);

  function runCommand(command: PaletteCommand | undefined) {
    if (!command || command.disabled) return;
    onClose();
    command.run();
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (!results.length) return;
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        setActiveIndex((safeIndex + 1) % results.length);
        break;
      case "ArrowUp":
        event.preventDefault();
        setActiveIndex((safeIndex - 1 + results.length) % results.length);
        break;
      case "Home":
        event.preventDefault();
        setActiveIndex(0);
        break;
      case "End":
        event.preventDefault();
        setActiveIndex(results.length - 1);
        break;
      case "Enter":
        event.preventDefault();
        runCommand(active);
        break;
    }
  }

  // Group consecutive results (already sorted by group) for rendering.
  const groups: Array<{
    group: PaletteCommand["group"];
    items: Array<{ command: PaletteCommand; index: number }>;
  }> = [];
  results.forEach((command, index) => {
    const last = groups.at(-1);
    if (last && last.group === command.group) last.items.push({ command, index });
    else groups.push({ group: command.group, items: [{ command, index }] });
  });

  return (
    <>
      <div className="flex items-center gap-2 border-b border-default px-3">
        <Search aria-hidden className="size-4 shrink-0 text-muted" />
        {/* Raw input on purpose: an ARIA 1.2 combobox with aria-activedescendant into the listbox
            below, styled as a borderless search row; the Input primitive's bordered field doesn't fit. */}
        <input
          role="combobox"
          aria-label="Search commands"
          aria-expanded={results.length > 0}
          aria-controls={listboxId}
          aria-autocomplete="list"
          aria-activedescendant={safeIndex >= 0 ? optionId(safeIndex) : undefined}
          autoFocus
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setActiveIndex(0);
          }}
          onKeyDown={onKeyDown}
          placeholder="Search commands"
          className="h-11 min-w-0 flex-1 bg-transparent text-sm text-fg placeholder:text-muted focus-visible:outline-none"
        />
        <Kbd shortcut="esc" />
      </div>
      <div
        ref={listRef}
        id={listboxId}
        role="listbox"
        aria-label="Commands"
        className="min-h-0 flex-1 overflow-y-auto p-1.5 empty:hidden"
      >
        {groups.map(({ group, items }) => {
          const groupLabelId = `${baseId}-group-${group}`;
          return (
            <div key={group} role="group" aria-labelledby={groupLabelId} className="py-1">
              <div id={groupLabelId} role="presentation" className="label-caps px-2 pt-1 pb-1.5">
                {group}
              </div>
              {items.map(({ command, index }) => (
                <div
                  key={command.id}
                  id={optionId(index)}
                  role="option"
                  aria-selected={index === safeIndex}
                  aria-disabled={command.disabled || undefined}
                  onMouseMove={() => index !== safeIndex && setActiveIndex(index)}
                  onClick={() => runCommand(command)}
                  className={cn(
                    "flex h-9 cursor-default items-center gap-2 rounded-sm px-2 text-sm select-none",
                    index === safeIndex ? "bg-surface-2 text-fg" : "text-fg-secondary",
                    command.disabled && "text-muted",
                  )}
                >
                  <span className="min-w-0 flex-1 truncate">{command.label}</span>
                  {command.disabled ? (
                    <span className="max-w-[45%] shrink-0 truncate text-2xs text-muted">
                      {command.disabledReason ?? DEFAULT_DISABLED_REASON}
                    </span>
                  ) : command.shortcut ? (
                    <Kbd shortcut={command.shortcut} className="shrink-0" />
                  ) : null}
                </div>
              ))}
            </div>
          );
        })}
      </div>
      {results.length === 0 ? (
        <p role="status" className="px-3 pb-8 text-center text-sm text-fg-secondary">
          {commands.length === 0
            ? "No commands are available here yet."
            : `No commands match “${query}”. Try a section name such as “capacity”.`}
        </p>
      ) : null}
      <div className="flex items-center gap-3 border-t border-default px-3 py-2 text-2xs text-muted">
        <span className="flex items-center gap-1">
          <Kbd shortcut="↑" />
          <Kbd shortcut="↓" /> to move
        </span>
        <span className="flex items-center gap-1">
          <Kbd shortcut="enter" /> to run
        </span>
      </div>
    </>
  );
}
