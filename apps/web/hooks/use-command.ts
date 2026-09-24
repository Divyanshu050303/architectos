/**
 * Command palette registry (spec §61, §106). Any mounted component can contribute
 * commands; the palette lists whatever is registered right now.
 *
 * Registrations are keyed by the registering hook instance, so a component that
 * unmounts takes its commands with it. The registry stores a snapshot of each
 * command's display data; `run` always calls the latest callback the component
 * rendered with, so passing a fresh array every render is fine and cheap.
 */
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useId, useMemo, useRef } from "react";
import { create } from "zustand";

export type PaletteCommandGroup = "Navigation" | "Architecture" | "Analysis" | "Project" | "Preferences";

export const PALETTE_GROUP_ORDER: readonly PaletteCommandGroup[] = [
  "Navigation",
  "Architecture",
  "Analysis",
  "Project",
  "Preferences",
];

export interface PaletteCommand {
  id: string;
  label: string;
  group: PaletteCommandGroup;
  /** e.g. "mod+shift+v" (lib/keyboard format). Display only: the owner handles the key binding. */
  shortcut?: string;
  keywords?: string[];
  disabled?: boolean;
  /**
   * Why the command cannot run right now, shown next to it in the palette (e.g. "Nothing to undo",
   * "Save or discard changes first"). Only read when `disabled` is true.
   */
  disabledReason?: string;
  /**
   * A project-wide stand-in (e.g. "Analyze capacity" that navigates to the capacity page). Any
   * non-fallback command with the same id replaces it, whatever the registration order, so the
   * mounted page's own action wins while it is on screen.
   */
  fallback?: boolean;
  run: () => void;
}

/** Shown for a disabled command that does not say why. */
export const DEFAULT_DISABLED_REASON = "Unavailable here";

interface CommandRegistryState {
  registrations: ReadonlyMap<string, readonly PaletteCommand[]>;
  register: (owner: string, commands: readonly PaletteCommand[]) => void;
  unregister: (owner: string) => void;
}

export const useCommandRegistry = create<CommandRegistryState>()((set) => ({
  registrations: new Map(),
  register: (owner, commands) =>
    set((state) => {
      const next = new Map(state.registrations);
      next.set(owner, commands);
      return { registrations: next };
    }),
  unregister: (owner) =>
    set((state) => {
      if (!state.registrations.has(owner)) return state;
      const next = new Map(state.registrations);
      next.delete(owner);
      return { registrations: next };
    }),
}));

/** Everything the palette displays; `run` is excluded because it is resolved at call time. */
function signature(commands: readonly PaletteCommand[]): string {
  return JSON.stringify(
    commands.map(({ id, label, group, shortcut, keywords, disabled, disabledReason, fallback }) => [
      id,
      label,
      group,
      shortcut ?? "",
      keywords ?? [],
      Boolean(disabled),
      disabledReason ?? "",
      Boolean(fallback),
    ]),
  );
}

/** Register commands while the calling component is mounted. */
export function useRegisterCommands(commands: readonly PaletteCommand[]): void {
  const owner = useId();
  const latest = useRef(commands);
  useEffect(() => {
    latest.current = commands;
  });

  const key = signature(commands);
  useEffect(() => {
    const snapshot = latest.current.map((command) => ({
      ...command,
      run: () => latest.current.find((c) => c.id === command.id)?.run(),
    }));
    useCommandRegistry.getState().register(owner, snapshot);
    // `key` captures every displayed field; `run` is read through the ref.
  }, [owner, key]);

  useEffect(() => () => useCommandRegistry.getState().unregister(owner), [owner]);
}

/**
 * All registered commands. A later registration with the same id replaces an earlier one, except
 * that a `fallback` command never replaces a non-fallback one.
 */
export function usePaletteCommands(): PaletteCommand[] {
  const registrations = useCommandRegistry((state) => state.registrations);
  return useMemo(() => mergeCommands(registrations.values()), [registrations]);
}

export function mergeCommands(registrations: Iterable<readonly PaletteCommand[]>): PaletteCommand[] {
  const byId = new Map<string, PaletteCommand>();
  for (const commands of registrations) {
    for (const command of commands) {
      const existing = byId.get(command.id);
      if (existing && command.fallback && !existing.fallback) continue;
      byId.delete(command.id);
      byId.set(command.id, command);
    }
  }
  return [...byId.values()];
}

/**
 * Page actions triggered from anywhere (spec §61): a project-wide palette command navigates to
 * `pageActionHref(page, action)` and the page runs the action once it is ready.
 */
export const PAGE_ACTION_PARAM = "action";

export type PageAction = "generate" | "analyze";

export function pageActionHref(href: string, action: PageAction): string {
  return `${href}${href.includes("?") ? "&" : "?"}${PAGE_ACTION_PARAM}=${action}`;
}

/**
 * Runs `run` once when the URL asks for `action` and `ready` is true, then removes the parameter
 * (replace, not push) so a refresh or Back does not repeat the action.
 */
export function usePageAction(action: PageAction, run: () => void, ready: boolean): void {
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const requested = searchParams.get(PAGE_ACTION_PARAM) === action;
  const latest = useRef(run);
  useEffect(() => {
    latest.current = run;
  });
  const handled = useRef(false);

  useEffect(() => {
    if (!requested || !ready || handled.current) return;
    handled.current = true;
    const params = new URLSearchParams(searchParams.toString());
    params.delete(PAGE_ACTION_PARAM);
    const query = params.toString();
    router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
    latest.current();
  }, [requested, ready, searchParams, router, pathname]);

  // A later request (e.g. the palette command again) may run it again.
  useEffect(() => {
    if (!requested) handled.current = false;
  }, [requested]);
}
