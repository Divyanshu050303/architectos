import type { AnalysisMode, ArchitectureVersionSummary } from "@/types/architecture";
import type { ComponentDefinition } from "@/types/component";

import type { AutoLayoutKind } from "../../utils/graph-layout";
import type { GraphView } from "../../utils/graph-view";

export interface ArchitectureToolbarProps {
  editable: boolean;
  mode: AnalysisMode;
  onModeChange: (mode: AnalysisMode) => void;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onFitView: () => void;
  showGrid: boolean;
  onToggleGrid: () => void;
  showMiniMap: boolean;
  onToggleMiniMap: () => void;
  isFullscreen: boolean;
  onToggleFullscreen: () => void;
  canUndo: boolean;
  canRedo: boolean;
  onUndo: () => void;
  onRedo: () => void;
  onAutoLayout: (kind: AutoLayoutKind) => void;
  /** Whether components carry a domain: enables Overview and Domain grouped layout. */
  domainsAvailable: boolean;
  view: GraphView;
  onViewChange: (view: GraphView) => void;
  focusHops: 1 | 2;
  onFocusHopsChange: (hops: 1 | 2) => void;
  versions: readonly ArchitectureVersionSummary[];
  onCompare: (from: number | null, to: number | null) => void;
  onAddComponent: (definition: ComponentDefinition) => void;
  pendingCount: number;
  savedVersion: number | null;
  isSaving: boolean;
  onSave: () => void;
  onDiscard: () => void;
  inspectorOpen: boolean;
  onToggleInspector: () => void;
}
