import { ProvenanceTag } from "@/components/feedback/ProvenanceTag";
import { AppearListItem } from "@/components/feedback/AppearListItem";
import { Badge } from "@/components/ui/badge";
import type { SecurityControls, StrideCategory } from "@/types/security";

import { EXPOSURE_BADGE } from "../../utils/node-transform";
import { Fact, SEVERITY_META, WhyButton } from "./shared";
import type { NodeInspectorProps, NodeSecurityDetails } from "./types";

const STRIDE_LABELS: Record<StrideCategory, string> = {
  spoofing: "Spoofing",
  tampering: "Tampering",
  repudiation: "Repudiation",
  information_disclosure: "Information disclosure",
  denial_of_service: "Denial of service",
  elevation_of_privilege: "Elevation of privilege",
};

const CONTROL_ROWS: readonly { key: keyof Omit<SecurityControls, "nodeId" | "handlesPii">; label: string }[] =
  [
    { key: "authentication", label: "Authentication" },
    { key: "authorization", label: "Authorization" },
    { key: "encryptionInTransit", label: "Encryption in transit" },
    { key: "encryptionAtRest", label: "Encryption at rest" },
    { key: "secretsManagement", label: "Secrets management" },
  ];

function ControlValue({ value }: { value: boolean | null }) {
  if (value === null) return <span className="text-muted">Unknown</span>;
  return value ? (
    <span className="text-accent-fg">Yes</span>
  ) : (
    <span className="font-medium text-danger-fg">No</span>
  );
}

export function SecurityTab({
  security,
  onOpenEvidence,
}: { security: NodeSecurityDetails } & Pick<NodeInspectorProps, "onOpenEvidence">) {
  const { exposure, controls, threats } = security;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <span className="text-xs text-muted">From the security rules</span>
        <ProvenanceTag kind="calculated" />
      </div>
      {exposure ? (
        <section className="flex flex-col gap-1.5">
          <h3 className="label-caps">Exposure</h3>
          <div className="flex items-center gap-2">
            <Badge tone={EXPOSURE_BADGE[exposure.level].tone}>{EXPOSURE_BADGE[exposure.level].label}</Badge>
          </div>
          <p className="text-xs text-fg-secondary">{exposure.reason}</p>
        </section>
      ) : null}
      {controls ? (
        <section className="flex flex-col gap-1.5">
          <h3 className="label-caps">Controls</h3>
          <dl className="divide-y divide-default">
            {CONTROL_ROWS.map((row) => (
              <Fact key={row.key} label={row.label}>
                <ControlValue value={controls[row.key]} />
              </Fact>
            ))}
            <Fact label="Handles personal data">{controls.handlesPii ? "Yes" : "No"}</Fact>
          </dl>
        </section>
      ) : null}
      <section className="flex flex-col gap-1.5">
        <h3 className="label-caps">Threats</h3>
        {threats.length === 0 ? (
          <p className="text-xs text-muted">No threats reference this component.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {threats.map((threat, index) => {
              const severity = SEVERITY_META[threat.severity];
              return (
                <AppearListItem
                  key={threat.id}
                  index={index}
                  className="flex flex-col gap-1.5 rounded-md border border-default p-2.5"
                >
                  <div className="flex items-center gap-2">
                    <Badge tone={severity.tone}>{severity.label}</Badge>
                    <span className="label-caps">{STRIDE_LABELS[threat.category]}</span>
                  </div>
                  <p className="text-sm font-medium text-fg">{threat.title}</p>
                  <p className="text-xs text-fg">
                    <span className="text-muted">Mitigation: </span>
                    {threat.mitigation}
                  </p>
                  {threat.evidenceId ? (
                    <div>
                      <WhyButton
                        evidenceId={threat.evidenceId}
                        label={threat.title}
                        onOpen={onOpenEvidence}
                      />
                    </div>
                  ) : null}
                </AppearListItem>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
