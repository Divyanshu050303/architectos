import { cn } from "@/lib/utils";

export function Table({ className, ...props }: React.TableHTMLAttributes<HTMLTableElement>) {
  return (
    // Focusable so keyboard users can scroll wide tables at narrow widths (WCAG 2.1.1).
    <div className="w-full overflow-x-auto" tabIndex={0}>
      <table className={cn("w-full border-collapse text-sm", className)} {...props} />
    </div>
  );
}

export function Th({ className, ...props }: React.ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      scope="col"
      className={cn("label-caps border-b border-default px-3 py-2 text-left font-semibold", className)}
      {...props}
    />
  );
}

export function Td({ className, ...props }: React.TdHTMLAttributes<HTMLTableCellElement>) {
  return <td className={cn("border-b border-default px-3 py-2 align-top text-fg", className)} {...props} />;
}
