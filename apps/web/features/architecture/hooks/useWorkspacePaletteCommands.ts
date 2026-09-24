/** Workspace actions contributed to the ⌘K command palette while the workspace is mounted. */
import { useRouter } from "next/navigation";

import { type PaletteCommand, useRegisterCommands } from "@/hooks/use-command";
import { useCommandStore } from "@/stores/command-store";
import type { ComponentDefinition } from "@/types/component";

import { focusCommandBar } from "../components/CommandBar";
import { COMPONENT_LIBRARY } from "../constants";
import { AUTO_LAYOUT_LABELS, type AutoLayoutKind } from "../utils/graph-layout";

export interface WorkspacePaletteActions {
  projectId: string;
  editable: boolean;
  canUndo: boolean;
  canRedo: boolean;
  isDirty: boolean;
  addComponent: (definition: ComponentDefinition) => void;
  autoLayout: (kind: AutoLayoutKind) => void;
  fitView: () => void;
  /** Opens the version comparison; null when there is nothing to compare yet. */
  compareVersions: (() => void) | null;
  undo: () => void;
  redo: () => void;
  save: () => void;
}

export function useWorkspacePaletteCommands(actions: WorkspacePaletteActions) {
  const router = useRouter();
  const { projectId, editable, canUndo, canRedo, isDirty } = actions;

  // Spec §61: disabled commands say why, not a generic "unavailable".
  const readOnly = "Read-only here: viewing an older version or a small screen";
  const reason = (checks: [boolean, string][]) => checks.find(([blocked]) => blocked)?.[1];

  const commands: PaletteCommand[] = [
    ...COMPONENT_LIBRARY.map((definition): PaletteCommand => ({
      id: `architecture.add.${definition.type}.${definition.name}`,
      label: `Add component… ${definition.name}`,
      group: "Architecture",
      keywords: ["add", "component", definition.type, definition.technology],
      disabled: !editable,
      disabledReason: readOnly,
      run: () => actions.addComponent(definition),
    })),
    ...(Object.keys(AUTO_LAYOUT_LABELS) as AutoLayoutKind[]).map((kind): PaletteCommand => ({
      id: `architecture.layout.${kind}`,
      label: `Auto layout: ${AUTO_LAYOUT_LABELS[kind].replace(" · ", " ").toLowerCase()}`,
      group: "Architecture",
      keywords: ["layout", "arrange", kind === "force" ? "force" : kind === "domain" ? "domain" : "dagre"],
      disabled: !editable,
      disabledReason: readOnly,
      run: () => actions.autoLayout(kind),
    })),
    { id: "architecture.fit", label: "Fit view", group: "Architecture", shortcut: "f", run: actions.fitView },
    {
      id: "architecture.undo",
      label: "Undo",
      group: "Architecture",
      shortcut: "mod+z",
      disabled: !editable || !canUndo,
      disabledReason: reason([
        [!editable, readOnly],
        [!canUndo, "Nothing to undo"],
      ]),
      run: actions.undo,
    },
    {
      id: "architecture.redo",
      label: "Redo",
      group: "Architecture",
      shortcut: "mod+shift+z",
      disabled: !editable || !canRedo,
      disabledReason: reason([
        [!editable, readOnly],
        [!canRedo, "Nothing to redo"],
      ]),
      run: actions.redo,
    },
    {
      id: "architecture.save",
      label: "Save architecture",
      group: "Architecture",
      shortcut: "mod+s",
      disabled: !editable || !isDirty,
      disabledReason: reason([
        [!editable, readOnly],
        [!isDirty, "No unsaved changes"],
      ]),
      run: actions.save,
    },
    {
      id: "architecture.explain",
      label: "Explain architecture",
      group: "Architecture",
      keywords: ["ai", "ask", "describe"],
      run: () => {
        useCommandStore.getState().setPrompt("Explain this architecture.");
        focusCommandBar();
      },
    },
    {
      id: "architecture.compare-versions",
      label: "Compare versions",
      group: "Architecture",
      keywords: ["diff", "version", "history", "changes"],
      disabled: actions.compareVersions === null,
      disabledReason: "Needs at least two versions",
      run: () => actions.compareVersions?.(),
    },
    {
      id: "architecture.validate",
      label: "Validate architecture",
      group: "Analysis",
      keywords: ["findings", "health", "check"],
      run: () => router.push(`/project/${projectId}/validation`),
    },
  ];

  useRegisterCommands(commands);
}
