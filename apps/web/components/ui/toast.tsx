"use client";

import { X } from "lucide-react";
import { Toast as RadixToast } from "radix-ui";
import { create } from "zustand";

import { cn } from "@/lib/utils";

export type ToastTone = "neutral" | "success" | "danger";

interface ToastItem {
  id: number;
  title: string;
  description?: string;
  tone: ToastTone;
}

interface ToastStore {
  toasts: ToastItem[];
  push: (toast: Omit<ToastItem, "id">) => void;
  dismiss: (id: number) => void;
}

let nextId = 1;

const useToastStore = create<ToastStore>((set) => ({
  toasts: [],
  push: (toast) => set((s) => ({ toasts: [...s.toasts, { ...toast, id: nextId++ }] })),
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));

export function toast(title: string, options: { description?: string; tone?: ToastTone } = {}) {
  useToastStore.getState().push({ title, description: options.description, tone: options.tone ?? "neutral" });
}

const TONE: Record<ToastTone, string> = {
  neutral: "border-l-control",
  success: "border-l-accent-strong",
  danger: "border-l-danger",
};

export function Toaster() {
  const toasts = useToastStore((s) => s.toasts);
  const dismiss = useToastStore((s) => s.dismiss);
  return (
    <RadixToast.Provider swipeDirection="right" duration={5000}>
      {toasts.map((t) => (
        <RadixToast.Root
          key={t.id}
          type={t.tone === "danger" ? "foreground" : "background"}
          onOpenChange={(open) => !open && dismiss(t.id)}
          className={cn(
            "flex items-start gap-3 rounded-md border border-l-2 border-default bg-surface px-3 py-2.5 shadow-raised",
            TONE[t.tone],
          )}
        >
          <div className="flex min-w-0 flex-1 flex-col gap-0.5">
            <RadixToast.Title className="text-sm font-medium text-fg">{t.title}</RadixToast.Title>
            {t.description ? (
              <RadixToast.Description className="text-xs text-muted">{t.description}</RadixToast.Description>
            ) : null}
          </div>
          <RadixToast.Close aria-label="Dismiss" className="rounded-sm p-0.5 text-muted hover:text-fg">
            <X aria-hidden className="size-3.5" />
          </RadixToast.Close>
        </RadixToast.Root>
      ))}
      <RadixToast.Viewport className="fixed right-4 bottom-4 z-[60] flex w-80 max-w-[calc(100vw-2rem)] flex-col gap-2 outline-none" />
    </RadixToast.Provider>
  );
}
