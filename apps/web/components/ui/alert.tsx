import { AlertTriangle, CheckCircle2, Info, OctagonAlert } from "lucide-react";

import { cn } from "@/lib/utils";

export type AlertTone = "info" | "success" | "warning" | "danger";

const TONES: Record<AlertTone, { className: string; Icon: typeof Info }> = {
  info: { className: "border-info/40 bg-info-soft text-info-fg", Icon: Info },
  success: { className: "border-accent/40 bg-accent-soft text-accent-fg", Icon: CheckCircle2 },
  warning: { className: "border-warning/40 bg-warning-soft text-warning-fg", Icon: AlertTriangle },
  danger: { className: "border-danger/40 bg-danger-soft text-danger-fg", Icon: OctagonAlert },
};

export interface AlertProps extends Omit<React.HTMLAttributes<HTMLDivElement>, "title"> {
  tone?: AlertTone;
  title: React.ReactNode;
  actions?: React.ReactNode;
}

export function Alert({ tone = "info", title, actions, className, children, ...props }: AlertProps) {
  const { className: toneClass, Icon } = TONES[tone];
  return (
    <div
      role={tone === "danger" ? "alert" : "status"}
      className={cn("flex gap-3 rounded-md border px-3 py-2.5 text-sm", toneClass, className)}
      {...props}
    >
      <Icon aria-hidden className="mt-0.5 size-4 shrink-0" />
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <p className="font-medium">{title}</p>
        {children ? <div className="text-fg-secondary">{children}</div> : null}
        {actions ? <div className="mt-1 flex gap-2">{actions}</div> : null}
      </div>
    </div>
  );
}
