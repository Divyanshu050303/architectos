import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CompareVersionsDialog } from "@/features/architecture/components/CompareVersionsDialog";
import { VersionConflictDialog } from "@/features/architecture/components/VersionConflictDialog";
import { useUiStore } from "@/stores/ui-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

import { queryResult } from "./operate-test-utils";

vi.mock("@/hooks/use-architecture", () => ({
  useArchitectureVersions: () => queryResult([{ version: 1 }, { version: 3 }, { version: 2 }]),
  useCompareVersions: () => ({ ...queryResult(null), isPending: true }),
}));

describe("VersionConflictDialog (spec §93)", () => {
  beforeEach(() => useUiStore.getState().reset());

  it("offers compare and reload, and no unbuilt branch action", () => {
    useUiStore.getState().showConflict({ yourVersion: 3, latestVersion: 4 });
    render(<VersionConflictDialog onReload={() => {}} onCompare={() => {}} />);
    const dialog = screen.getByRole("dialog", { name: "Architecture updated" });
    expect(within(dialog).getByRole("button", { name: "Compare" })).toBeEnabled();
    expect(within(dialog).getByRole("button", { name: "Reload" })).toBeEnabled();
    expect(within(dialog).queryByRole("button", { name: /branch/i })).not.toBeInTheDocument();
    expect(dialog).toHaveClass("motion-dialog");
  });
});

describe("CompareVersionsDialog (spec §73)", () => {
  beforeEach(() => useWorkspaceStore.getState().reset());

  it("picks versions with the design-system Select", async () => {
    useWorkspaceStore.getState().openCompare(null, 3);
    render(<CompareVersionsDialog projectId="proj_1" />);

    const from = screen.getByRole("combobox", { name: "From" });
    const to = screen.getByRole("combobox", { name: "To" });
    expect(to).toHaveValue("3");
    expect(
      within(from)
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual(["Choose a version", "v1", "v2", "v3"]);

    await userEvent.selectOptions(from, "2");
    expect(useWorkspaceStore.getState().compare).toEqual({ from: 2, to: 3 });
  });
});
