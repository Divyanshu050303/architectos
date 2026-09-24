/**
 * Keyboard shortcut helpers. Shortcuts are written as "mod+shift+z" where
 * `mod` is ⌘ on macOS and Ctrl elsewhere (spec §60).
 */

export interface ParsedShortcut {
  key: string;
  mod: boolean;
  shift: boolean;
  alt: boolean;
}

export function parseShortcut(shortcut: string): ParsedShortcut {
  const parts = shortcut.toLowerCase().split("+");
  const key = parts.at(-1) ?? "";
  return { key, mod: parts.includes("mod"), shift: parts.includes("shift"), alt: parts.includes("alt") };
}

export function isMacPlatform(): boolean {
  if (typeof navigator === "undefined") return true;
  return /mac|iphone|ipad/i.test(navigator.platform || navigator.userAgent);
}

interface KeyLike {
  key: string;
  metaKey: boolean;
  ctrlKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
}

export function matchesShortcut(event: KeyLike, shortcut: string, mac = isMacPlatform()): boolean {
  const s = parseShortcut(shortcut);
  const mod = mac ? event.metaKey : event.ctrlKey;
  if (s.mod !== mod || s.shift !== event.shiftKey || s.alt !== event.altKey) return false;
  if (!s.mod && (event.metaKey || event.ctrlKey)) return false;
  const key = event.key.toLowerCase();
  if (s.key === "delete") return key === "delete" || key === "backspace";
  if (s.key === "esc") return key === "escape";
  if (s.key === "space") return key === " ";
  return key === s.key;
}

const MAC_SYMBOLS: Record<string, string> = {
  mod: "⌘",
  shift: "⇧",
  alt: "⌥",
  enter: "↵",
  delete: "⌫",
  esc: "Esc",
};
const PC_SYMBOLS: Record<string, string> = {
  mod: "Ctrl",
  shift: "Shift",
  alt: "Alt",
  enter: "Enter",
  delete: "Del",
  esc: "Esc",
};

export function formatShortcut(shortcut: string, mac = isMacPlatform()): string {
  const symbols = mac ? MAC_SYMBOLS : PC_SYMBOLS;
  return shortcut
    .split("+")
    .map((part) => symbols[part.toLowerCase()] ?? part.toUpperCase())
    .join(mac ? "" : "+");
}

/** True when a key event originates from a text-entry control, so global shortcuts must not fire. */
export function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
}
