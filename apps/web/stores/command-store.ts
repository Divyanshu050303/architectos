/** AI command bar state (spec §30). The proposal itself is server state. */
import { create } from "zustand";

export const MAX_RECENT_PROMPTS = 20;

export type CommandStatus = "idle" | "submitting" | "error";

export interface CommandError {
  title: string;
  message: string;
  requestId?: string;
}

export interface CommandState {
  prompt: string;
  status: CommandStatus;
  activeProposalId: string | null;
  error: CommandError | null;
  /** Most recent first, de-duplicated. */
  recentPrompts: string[];
}

export interface CommandActions {
  setPrompt: (prompt: string) => void;
  submitStarted: () => void;
  submitSucceeded: (proposalId: string) => void;
  submitFailed: (error: CommandError) => void;
  clearProposal: () => void;
  reset: () => void;
}

export type CommandStore = CommandState & CommandActions;

const INITIAL_STATE: CommandState = {
  prompt: "",
  status: "idle",
  activeProposalId: null,
  error: null,
  recentPrompts: [],
};

export const useCommandStore = create<CommandStore>()((set) => ({
  ...INITIAL_STATE,

  setPrompt: (prompt) => set({ prompt }),

  submitStarted: () =>
    set((state) => {
      const prompt = state.prompt.trim();
      const recentPrompts = prompt
        ? [prompt, ...state.recentPrompts.filter((p) => p !== prompt)].slice(0, MAX_RECENT_PROMPTS)
        : state.recentPrompts;
      return { status: "submitting", error: null, recentPrompts };
    }),

  submitSucceeded: (proposalId) =>
    set({ status: "idle", activeProposalId: proposalId, prompt: "", error: null }),

  submitFailed: (error) => set({ status: "error", error }),

  clearProposal: () => set({ activeProposalId: null }),

  reset: () => set(INITIAL_STATE),
}));
