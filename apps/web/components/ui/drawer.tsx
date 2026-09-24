"use client";

import { X } from "lucide-react";
import { Dialog as RadixDialog } from "radix-ui";

import { cn } from "@/lib/utils";

import { useRestoreFocus } from "./dialog";

export const Drawer = RadixDialog.Root;
export const DrawerTrigger = RadixDialog.Trigger;

export interface DrawerContentProps extends React.ComponentProps<typeof RadixDialog.Content> {
  title: string;
  description?: string;
  side?: "right" | "bottom";
  /** Rendered as a secondary "evidence" panel (spec §124). */
  tone?: "default" | "evidence";
}

export function DrawerContent({
  title,
  description,
  side = "right",
  tone = "default",
  className,
  children,
  onOpenAutoFocus,
  onCloseAutoFocus,
  ...props
}: DrawerContentProps) {
  const focus = useRestoreFocus({ onOpenAutoFocus, onCloseAutoFocus });
  return (
    <RadixDialog.Portal>
      <RadixDialog.Overlay className="fixed inset-0 z-40 bg-overlay" />
      <RadixDialog.Content
        className={cn(
          "fixed z-50 flex flex-col border-default shadow-raised focus-visible:outline-none",
          tone === "evidence" ? "bg-surface-2" : "bg-surface",
          side === "right"
            ? "inset-y-0 right-0 w-full max-w-md border-l"
            : "inset-x-0 bottom-0 max-h-[80vh] rounded-t-xl border-t",
          className,
        )}
        {...(description ? {} : { "aria-describedby": undefined })}
        {...props}
        {...focus}
      >
        <div className="flex items-start justify-between gap-4 border-b border-default px-5 py-4">
          <div className="flex flex-col gap-1">
            <RadixDialog.Title className="label-caps">{title}</RadixDialog.Title>
            {description ? (
              <RadixDialog.Description className="text-xs text-muted">{description}</RadixDialog.Description>
            ) : null}
          </div>
          <RadixDialog.Close
            aria-label="Close"
            className="rounded-sm p-1 text-muted hover:bg-surface hover:text-fg"
          >
            <X aria-hidden className="size-4" />
          </RadixDialog.Close>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
      </RadixDialog.Content>
    </RadixDialog.Portal>
  );
}
