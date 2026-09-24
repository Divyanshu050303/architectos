/** Layout primitives shared by the report sections. */

export function ReportSection({
  title,
  tag,
  children,
}: {
  title: string;
  tag?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="flex flex-col gap-3 print:break-inside-avoid-page">
      <div className="flex items-center justify-between gap-3 border-b border-default pb-1.5">
        <h2 className="text-sm font-semibold">{title}</h2>
        {tag}
      </div>
      {children}
    </section>
  );
}

export function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="label-caps">{label}</dt>
      <dd className="tabular">{value}</dd>
    </div>
  );
}

export function Missing({ text }: { text: string }) {
  return <p className="text-sm text-muted">{text}</p>;
}
