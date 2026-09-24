"use client";

import { X } from "lucide-react";
import { Dialog as RadixDialog } from "radix-ui";
import { useEffect, useRef } from "react";

import { cn } from "@/lib/utils";

export const Dialog = RadixDialog.Root;
export const DialogTrigger = RadixDialog.Trigger;
export const DialogClose = RadixDialog.Close;

type AutoFocusHandler = (event: Event) => void;

/**
 * Overlays whose elements cannot be focus-restore targets: dialogs, and menus (a menu item that
 * opens a dialog unmounts with the menu, so focus goes back to the menu's trigger instead).
 */
const DIALOG_SELECTOR = '[role="dialog"],[role="alertdialog"],[role="menu"]';

/**
 * The last element focused outside any dialog or menu. Needed because a control with `autoFocus`
 * inside a dialog takes focus before Radix's onOpenAutoFocus runs.
 */
let lastFocusOutsideDialogs: HTMLElement | null = null;
let trackingFocus = false;

function trackFocusOutsideDialogs() {
  if (trackingFocus || typeof document === "undefined") return;
  trackingFocus = true;
  const active = document.activeElement;
  if (active instanceof HTMLElement && active !== document.body && !active.closest(DIALOG_SELECTOR)) {
    lastFocusOutsideDialogs = active;
  }
  document.addEventListener(
    "focusin",
    (event) => {
      const target = event.target;
      if (target instanceof HTMLElement && !target.closest(DIALOG_SELECTOR)) lastFocusOutsideDialogs = target;
    },
    true,
  );
}

/**
 * Give focus back to whatever had it when the dialog opened (spec §62, §80). Radix only restores
 * focus to a `<Dialog.Trigger>`; dialogs opened from a store, a shortcut or a menu have none, so
 * without this Esc would drop focus on <body>. Caller handlers run first and may preventDefault.
 */
export function useRestoreFocus(handlers: {
  onOpenAutoFocus?: AutoFocusHandler;
  onCloseAutoFocus?: AutoFocusHandler;
}): { onOpenAutoFocus: AutoFocusHandler; onCloseAutoFocus: AutoFocusHandler } {
  const previous = useRef<HTMLElement | null>(null);
  useEffect(trackFocusOutsideDialogs, []);
  return {
    onOpenAutoFocus: (event) => {
      const active = document.activeElement;
      previous.current =
        active instanceof HTMLElement && active !== document.body && !active.closest(DIALOG_SELECTOR)
          ? active
          : lastFocusOutsideDialogs;
      handlers.onOpenAutoFocus?.(event);
    },
    onCloseAutoFocus: (event) => {
      handlers.onCloseAutoFocus?.(event);
      if (event.defaultPrevented) return;
      // Radix skips onOpenAutoFocus when a child already took focus (autoFocus); focus is trapped
      // while the dialog is open, so the last element focused outside dialogs is the origin.
      const target = previous.current ?? lastFocusOutsideDialogs;
      previous.current = null;
      // A command run from this dialog may have opened another one; leave focus with it.
      if (document.querySelector('[role="dialog"][data-state="open"]')) {
        event.preventDefault();
        return;
      }
      if (target?.isConnected) {
        event.preventDefault();
        target.focus({ preventScroll: true });
      }
    },
  };
}

export interface DialogContentProps extends React.ComponentProps<typeof RadixDialog.Content> {
  title: string;
  description?: string;
  /** Hide the visible title while keeping it for screen readers. */
  hideTitle?: boolean;
}

export function DialogContent({
  title,
  description,
  hideTitle,
  className,
  children,
  onOpenAutoFocus,
  onCloseAutoFocus,
  ...props
}: DialogContentProps) {
  const focus = useRestoreFocus({ onOpenAutoFocus, onCloseAutoFocus });
  return (
    <RadixDialog.Portal>
      <RadixDialog.Overlay className="fixed inset-0 z-40 bg-overlay" />
      <RadixDialog.Content
        className={cn(
          "fixed top-1/2 left-1/2 z-50 flex max-h-[85vh] w-[calc(100vw-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2",
          "flex-col rounded-lg border border-default bg-surface shadow-raised focus-visible:outline-none",
          className,
        )}
        {...(description ? {} : { "aria-describedby": undefined })}
        {...props}
        {...focus}
      >
        <div className={cn("flex items-start justify-between gap-4 px-5 pt-4", hideTitle && "sr-only")}>
          <div className="flex flex-col gap-1">
            <RadixDialog.Title className="text-sm font-semibold text-fg">{title}</RadixDialog.Title>
            {description ? (
              <RadixDialog.Description className="text-xs text-muted">{description}</RadixDialog.Description>
            ) : null}
          </div>
          <RadixDialog.Close
            aria-label="Close"
            className="rounded-sm p-1 text-muted hover:bg-surface-2 hover:text-fg"
          >
            <X aria-hidden className="size-4" />
          </RadixDialog.Close>
        </div>
        <div className="min-h-0 overflow-y-auto px-5 pt-3 pb-5">{children}</div>
      </RadixDialog.Content>
    </RadixDialog.Portal>
  );
}

export function DialogFooter({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("mt-5 flex items-center justify-end gap-2", className)} {...props} />;
}
