"use client";

import { Check, ChevronDown } from "lucide-react";
import { forwardRef, useEffect, useId, useMemo, useRef, useState } from "react";

import { cn } from "@/lib/utils";

export interface ComboboxOption {
  value: string;
  label: string;
  /** Secondary line under the label; also searched. */
  description?: string;
  disabled?: boolean;
}

export interface ComboboxProps {
  options: readonly ComboboxOption[];
  /** Selected value; "" or an unknown value means nothing is selected. */
  value: string;
  onValueChange: (value: string) => void;
  /** Wire to a `<Field>` label via `id`, and pass its `aria-describedby`. */
  id?: string;
  "aria-label"?: string;
  "aria-labelledby"?: string;
  "aria-describedby"?: string;
  "aria-invalid"?: boolean;
  placeholder?: string;
  /** Accessible name of the popup list; defaults to `aria-label`, then "Options". */
  listLabel?: string;
  /** Shown in the list when the query matches nothing. */
  emptyMessage?: string;
  disabled?: boolean;
  name?: string;
  className?: string;
}

/** Case-insensitive; every whitespace-separated term must appear in the label or description. */
export function filterComboboxOptions(options: readonly ComboboxOption[], query: string): ComboboxOption[] {
  const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!terms.length) return [...options];
  return options.filter((option) => {
    const haystack = `${option.label} ${option.description ?? ""}`.toLowerCase();
    return terms.every((term) => haystack.includes(term));
  });
}

const CONTROL =
  "h-8 w-full rounded-sm border border-strong bg-surface pr-8 pl-2.5 text-sm text-fg placeholder:text-muted " +
  "hover:border-control focus-visible:border-accent-strong focus-visible:outline-none focus-visible:ring-2 " +
  "focus-visible:ring-accent/30 disabled:opacity-50 aria-[invalid=true]:border-danger";

/**
 * A searchable single select (spec §73), following the WAI-ARIA combobox pattern with a listbox
 * popup: focus stays in the input and the active option is exposed through
 * `aria-activedescendant`.
 *
 * Keys: ↓/↑ open and move (wrapping), Home/End jump, Enter selects, Esc closes (a second Esc
 * clears the query), Tab closes without changing the value. Typing filters the list.
 */
export const Combobox = forwardRef<HTMLInputElement, ComboboxProps>(function Combobox(
  {
    options,
    value,
    onValueChange,
    id,
    placeholder = "Search…",
    emptyMessage = "No matches.",
    listLabel,
    disabled,
    name,
    className,
    ...aria
  },
  ref,
) {
  const baseId = useId();
  const inputId = id ?? `${baseId}-input`;
  const listboxId = `${baseId}-listbox`;
  const optionId = (index: number) => `${baseId}-option-${index}`;
  const listRef = useRef<HTMLUListElement>(null);

  const selected = options.find((option) => option.value === value);
  const [open, setOpen] = useState(false);
  // null: not searching, the input shows the selected label.
  const [query, setQuery] = useState<string | null>(null);
  const [activeIndex, setActiveIndex] = useState(-1);

  const results = useMemo(() => filterComboboxOptions(options, query ?? ""), [options, query]);
  const safeIndex = open && results.length ? Math.min(Math.max(activeIndex, 0), results.length - 1) : -1;

  useEffect(() => {
    if (safeIndex < 0) return;
    const node = listRef.current?.querySelectorAll<HTMLElement>('[role="option"]')[safeIndex];
    node?.scrollIntoView?.({ block: "nearest" });
  }, [safeIndex]);

  const moveTo = setActiveIndex;

  function openList(index?: number) {
    if (disabled) return;
    const selectedIndex = results.findIndex((option) => option.value === value);
    setOpen(true);
    moveTo(index ?? (selectedIndex >= 0 ? selectedIndex : 0));
  }

  function close() {
    setOpen(false);
    setQuery(null);
    setActiveIndex(-1);
  }

  function choose(option: ComboboxOption | undefined) {
    if (!option || option.disabled) return;
    if (option.value !== value) onValueChange(option.value);
    close();
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        if (!open) openList();
        else if (results.length) moveTo((safeIndex + 1) % results.length);
        break;
      case "ArrowUp":
        event.preventDefault();
        if (!open) openList(results.length - 1);
        else if (results.length) moveTo((safeIndex - 1 + results.length) % results.length);
        break;
      case "Home":
        if (!open) return;
        event.preventDefault();
        moveTo(0);
        break;
      case "End":
        if (!open) return;
        event.preventDefault();
        moveTo(results.length - 1);
        break;
      case "Enter":
        if (!open) return;
        // Inside a form, Enter on an open list selects instead of submitting.
        event.preventDefault();
        choose(results[safeIndex]);
        break;
      case "Escape":
        if (open) {
          event.preventDefault();
          // Keep dialogs that contain the combobox open.
          event.stopPropagation();
          close();
        } else if (query !== null) {
          event.preventDefault();
          setQuery(null);
        }
        break;
      case "Tab":
        if (open) close();
        break;
    }
  }

  const active = safeIndex >= 0 ? results[safeIndex] : undefined;

  return (
    <div className={cn("relative", className)}>
      <input
        ref={ref}
        id={inputId}
        name={name}
        type="text"
        role="combobox"
        autoComplete="off"
        aria-autocomplete="list"
        aria-expanded={open}
        aria-controls={listboxId}
        aria-activedescendant={active ? optionId(safeIndex) : undefined}
        aria-label={aria["aria-label"]}
        aria-labelledby={aria["aria-labelledby"]}
        aria-describedby={aria["aria-describedby"]}
        aria-invalid={aria["aria-invalid"] || undefined}
        disabled={disabled}
        placeholder={selected ? selected.label : placeholder}
        value={query ?? selected?.label ?? ""}
        onChange={(event) => {
          setQuery(event.target.value);
          setOpen(true);
          setActiveIndex(0);
        }}
        onKeyDown={onKeyDown}
        onClick={() => {
          if (!open) openList();
        }}
        onBlur={(event) => {
          // Clicking an option keeps focus (mousedown is prevented), so a real blur always closes.
          if (!event.currentTarget.parentElement?.contains(event.relatedTarget)) close();
        }}
        className={CONTROL}
      />
      <ChevronDown
        aria-hidden
        className="pointer-events-none absolute top-1/2 right-2.5 size-4 -translate-y-1/2 text-muted"
      />
      <ul
        ref={listRef}
        id={listboxId}
        role="listbox"
        aria-label={listLabel ?? aria["aria-label"] ?? "Options"}
        hidden={!open}
        className={cn(
          "absolute inset-x-0 top-full z-50 mt-1 max-h-64 overflow-y-auto rounded-md border border-default",
          "bg-surface p-1 shadow-raised",
        )}
      >
        {results.map((option, index) => (
          <li
            key={option.value}
            id={optionId(index)}
            role="option"
            aria-selected={option.value === value}
            aria-disabled={option.disabled || undefined}
            data-active={index === safeIndex || undefined}
            onMouseDown={(event) => event.preventDefault()}
            onMouseMove={() => index !== safeIndex && setActiveIndex(index)}
            onClick={() => choose(option)}
            className={cn(
              "flex cursor-default items-start gap-2 rounded-sm px-2 py-1.5 text-sm text-fg select-none",
              "data-[active]:bg-surface-2 aria-disabled:opacity-40",
            )}
          >
            <Check
              aria-hidden
              className={cn("mt-0.5 size-3.5 shrink-0 text-accent-fg", option.value !== value && "invisible")}
            />
            <span className="flex min-w-0 flex-col">
              <span className="truncate">{option.label}</span>
              {option.description ? (
                <span className="line-clamp-2 text-xs text-muted">{option.description}</span>
              ) : null}
            </span>
          </li>
        ))}
        {results.length === 0 ? (
          <li role="presentation" className="px-2 py-1.5 text-sm text-muted">
            {emptyMessage}
          </li>
        ) : null}
      </ul>
    </div>
  );
});
