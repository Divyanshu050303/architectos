import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { Workflow } from "lucide-react";

import { Button } from "../ui/button";
import { EmptyState } from "./EmptyState";
import { ErrorState } from "./ErrorState";
import { LoadingSteps } from "./LoadingSteps";
import { ProvenanceTag } from "./ProvenanceTag";

const meta: Meta = { title: "Feedback/States" };
export default meta;

type Story = StoryObj;

export const Empty: Story = {
  render: () => (
    <EmptyState
      icon={Workflow}
      title="No architecture yet."
      description="Describe your system and ArchitectOS will generate a starting architecture."
      action={<Button variant="primary">Describe system</Button>}
    />
  ),
};

export const Error: Story = {
  render: () => (
    <ErrorState
      title="Architecture generation failed."
      message="The AI response did not satisfy the Architecture IR schema."
      requestId="req_8d2f"
      noChangesApplied
      onRetry={() => {}}
      details="nodes[3].type: expected one of client, cdn, load_balancer…"
    />
  ),
};

export const AiProgress: Story = {
  render: () => (
    <LoadingSteps
      title="Generating architecture"
      steps={[
        { id: "1", label: "Analyzing requirements", status: "done" },
        { id: "2", label: "Checking assumptions", status: "done" },
        { id: "3", label: "Selecting components", status: "done" },
        { id: "4", label: "Building architecture", status: "running" },
        { id: "5", label: "Validating architecture", status: "pending" },
      ]}
    />
  ),
};

export const TrustModel: Story = {
  render: () => (
    <div className="flex flex-wrap gap-2">
      <ProvenanceTag kind="fact" />
      <ProvenanceTag kind="ai" />
      <ProvenanceTag kind="calculated" />
      <ProvenanceTag kind="finding" />
      <ProvenanceTag kind="evidence" />
    </div>
  ),
};
