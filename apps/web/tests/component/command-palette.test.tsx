import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CommandPalette } from "@/components/command/CommandPalette";
import {
  mergeCommands,
  type PaletteCommand,
  pageActionHref,
  useCommandRegistry,
  useRegisterCommands,
} from "@/hooks/use-command";
import { useUiStore } from "@/stores/ui-store";

function Registrar({ commands }: { commands: PaletteCommand[] }) {
  useRegisterCommands(commands);
  return null;
}

function setup(commands: PaletteCommand[]) {
  const view = render(
    <>
      <Registrar commands={commands} />
      <CommandPalette />
    </>,
  );
  act(() => useUiStore.getState().setCommandPaletteOpen(true));
  return view;
}

describe("CommandPalette", () => {
  const goCapacity = vi.fn();
  const goValidation = vi.fn();
  const simulate = vi.fn();
  let commands: PaletteCommand[];

  beforeEach(() => {
    goCapacity.mockReset();
    goValidation.mockReset();
    simulate.mockReset();
    commands = [
      { id: "nav.capacity", label: "Go to Capacity", group: "Navigation", run: goCapacity },
      {
        id: "nav.validation",
        label: "Go to Validation",
        group: "Navigation",
        keywords: ["findings"],
        shortcut: "mod+shift+v",
        run: goValidation,
      },
      {
        id: "sim.run",
        label: "Run simulation",
        group: "Analysis",
        disabled: true,
        disabledReason: "A simulation is running",
        run: simulate,
      },
    ];
  });

  afterEach(() => {
    useUiStore.getState().reset();
  });

  it("lists commands grouped, and filters by label and keywords", async () => {
    setup(commands);
    const input = screen.getByRole("combobox", { name: "Search commands" });
    expect(screen.getByRole("group", { name: "Navigation" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Analysis" })).toBeInTheDocument();
    expect(screen.getAllByRole("option")).toHaveLength(3);

    await userEvent.type(input, "findings");
    const options = screen.getAllByRole("option");
    expect(options).toHaveLength(1);
    expect(options[0]).toHaveTextContent("Go to Validation");

    await userEvent.clear(input);
    await userEvent.type(input, "nothing-matches");
    expect(screen.queryAllByRole("option")).toHaveLength(0);
    expect(screen.getByRole("status")).toHaveTextContent("No commands match");
  });

  it("moves with arrow keys and runs the active command on Enter", async () => {
    setup(commands);
    const input = screen.getByRole("combobox");
    expect(screen.getAllByRole("option")[0]).toHaveAttribute("aria-selected", "true");

    await userEvent.keyboard("{ArrowDown}");
    const second = screen.getAllByRole("option")[1];
    expect(second).toHaveAttribute("aria-selected", "true");
    expect(input).toHaveAttribute("aria-activedescendant", second?.id);

    await userEvent.keyboard("{Enter}");
    expect(goValidation).toHaveBeenCalledTimes(1);
    expect(goCapacity).not.toHaveBeenCalled();
    expect(useUiStore.getState().commandPaletteOpen).toBe(false);
  });

  it("explains disabled commands and never runs them", async () => {
    setup(commands);
    const option = screen.getByRole("option", { name: /Run simulation/ });
    expect(option).toHaveAttribute("aria-disabled", "true");
    expect(option).toHaveTextContent("A simulation is running");
    expect(option).not.toHaveTextContent("Available in V2");

    await userEvent.keyboard("{ArrowUp}"); // wraps to the last (disabled) item
    expect(option).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{Enter}");
    await userEvent.click(option);
    expect(simulate).not.toHaveBeenCalled();
    expect(useUiStore.getState().commandPaletteOpen).toBe(true);
  });

  it("opens with mod+k and unregisters commands on unmount", async () => {
    const { unmount } = render(
      <>
        <Registrar commands={commands} />
        <CommandPalette />
      </>,
    );
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    await userEvent.keyboard("{Control>}k{/Control}");
    await userEvent.keyboard("{Meta>}k{/Meta}");
    // One of the two chords is "mod" for the current platform, the other is ignored.
    expect(screen.getByRole("combobox")).toBeInTheDocument();

    unmount();
    expect(useCommandRegistry.getState().registrations.size).toBe(0);
  });

  it("gives focus back to the element that had it when closed with Escape", async () => {
    render(
      <>
        <button type="button">Origin</button>
        <Registrar commands={commands} />
        <CommandPalette />
      </>,
    );
    const origin = screen.getByRole("button", { name: "Origin" });
    origin.focus();
    await userEvent.keyboard("{Control>}k{/Control}");
    if (!screen.queryByRole("combobox")) await userEvent.keyboard("{Meta>}k{/Meta}");
    expect(screen.getByRole("combobox")).toHaveFocus();

    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    await waitFor(() => expect(origin).toHaveFocus());
  });

  it("falls back to a generic reason when a disabled command gives none", () => {
    setup([{ id: "x", label: "Undo", group: "Architecture", disabled: true, run: vi.fn() }]);
    expect(screen.getByRole("option", { name: /Undo/ })).toHaveTextContent("Unavailable here");
  });
});

describe("mergeCommands", () => {
  const run = () => {};
  it("lets a page command replace a project-wide fallback regardless of registration order", () => {
    const fallback: PaletteCommand = {
      id: "analysis.capacity.run",
      label: "Analyze capacity",
      group: "Analysis",
      fallback: true,
      run,
    };
    const page: PaletteCommand = { ...fallback, fallback: false, disabled: true, disabledReason: "Running" };
    expect(mergeCommands([[page], [fallback]])).toEqual([page]);
    expect(mergeCommands([[fallback], [page]])).toEqual([page]);
  });

  it("keeps last-registration-wins between ordinary commands", () => {
    const a: PaletteCommand = { id: "a", label: "First", group: "Project", run };
    const b: PaletteCommand = { ...a, label: "Second" };
    expect(mergeCommands([[a], [b]])).toEqual([b]);
  });

  it("builds page action links", () => {
    expect(pageActionHref("/project/p1/capacity", "analyze")).toBe("/project/p1/capacity?action=analyze");
    expect(pageActionHref("/project/p1/requirements?x=1", "generate")).toBe(
      "/project/p1/requirements?x=1&action=generate",
    );
  });
});
