import { cn } from "@/lib/utils";

export function Section({
  id,
  labelledBy,
  className,
  children,
  tone = "plain",
}: {
  id?: string;
  labelledBy: string;
  className?: string;
  children: React.ReactNode;
  /** "muted" alternates the band background to separate sections without shadows (spec §18). */
  tone?: "plain" | "muted";
}) {
  return (
    <section
      id={id}
      aria-labelledby={labelledBy}
      className={cn(
        "scroll-mt-16 border-t border-default",
        tone === "muted" ? "bg-surface-2/60" : "bg-background",
        className,
      )}
    >
      <div className="mx-auto w-full max-w-6xl px-4 py-16 sm:px-6 sm:py-20">{children}</div>
    </section>
  );
}

/**
 * Splits a multi-sentence headline so the last sentence is muted:
 * "Change the load. Watch the architecture answer." → fg + muted. Single sentences stay
 * one tone. The accessible name is unchanged because the space stays in the text.
 */
export function TwoToneTitle({ children }: { children: string }) {
  const split = children.lastIndexOf(". ", children.length - 2);
  if (split === -1) return <>{children}</>;
  return (
    <>
      {children.slice(0, split + 2)}
      <span className="text-muted">{children.slice(split + 2)}</span>
    </>
  );
}

export function SectionHeader({
  id,
  eyebrow,
  title,
  description,
  aside,
  level = 2,
}: {
  id: string;
  eyebrow?: string;
  title: string;
  description?: React.ReactNode;
  aside?: React.ReactNode;
  level?: 1 | 2;
}) {
  const Heading = level === 1 ? "h1" : "h2";
  return (
    <div className="mb-10 flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
      <div className="flex max-w-2xl flex-col gap-3">
        {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
        <Heading
          id={id}
          className={cn("headline text-fg", level === 1 ? "text-3xl sm:text-5xl" : "text-3xl sm:text-4xl")}
        >
          <TwoToneTitle>{title}</TwoToneTitle>
        </Heading>
        {description ? <p className="text-base text-pretty text-fg-secondary">{description}</p> : null}
      </div>
      {aside ? <div className="shrink-0">{aside}</div> : null}
    </div>
  );
}

/**
 * A static product frame for screenshots. Composed from real UI components, never an
 * image. `inert` keeps the non-functional controls out of the tab order while the
 * content stays readable.
 */
export function MockFrame({
  title,
  path,
  className,
  bodyClassName,
  children,
}: {
  title: string;
  path: string;
  className?: string;
  bodyClassName?: string;
  children: React.ReactNode;
}) {
  return (
    <figure className={cn("m-0 flex min-w-0 flex-col gap-2", className)}>
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden rounded-lg border border-default bg-surface shadow-subtle">
        <div className="flex h-9 shrink-0 items-center gap-2 border-b border-default bg-surface px-3">
          <span aria-hidden className="flex gap-1">
            <span className="size-2 rounded-full bg-strong" />
            <span className="size-2 rounded-full bg-strong" />
            <span className="size-2 rounded-full bg-strong" />
          </span>
          <span className="tabular truncate text-2xs text-muted">{path}</span>
        </div>
        <div inert className={cn("min-w-0 flex-1", bodyClassName)}>
          {children}
        </div>
      </div>
      <figcaption className="text-xs text-muted">{title}</figcaption>
    </figure>
  );
}
