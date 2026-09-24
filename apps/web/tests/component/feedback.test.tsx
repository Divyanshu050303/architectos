import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { ErrorBoundary } from "@/components/feedback/ErrorBoundary";
import { ErrorState } from "@/components/feedback/ErrorState";
import { LoadingSteps } from "@/components/feedback/LoadingSteps";
import { ProvenanceTag } from "@/components/feedback/ProvenanceTag";

describe("ErrorState", () => {
  it("shows the request id, reassurance and retries on click", async () => {
    const onRetry = vi.fn();
    render(
      <ErrorState
        title="Architecture generation failed."
        message="The AI response did not satisfy the Architecture IR schema."
        requestId="req_8d2f"
        noChangesApplied
        onRetry={onRetry}
        details="schema error at nodes[0].type"
      />,
    );

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Architecture generation failed.");
    expect(alert).toHaveTextContent("No changes were applied.");
    expect(screen.getByText("req_8d2f")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledTimes(1);

    expect(screen.getByText("View details")).toBeInTheDocument();
  });

  it("omits retry and reassurance when not provided", () => {
    render(<ErrorState title="Failed." message="Try again later." />);
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
    expect(screen.queryByText("No changes were applied.")).not.toBeInTheDocument();
  });
});

describe("LoadingSteps", () => {
  it("announces each step's status politely", () => {
    render(
      <LoadingSteps
        title="Generating architecture"
        steps={[
          { id: "a", label: "Analyzing requirements", status: "done" },
          { id: "b", label: "Calculating capacity", status: "running" },
          { id: "c", label: "Running validation", status: "pending" },
          { id: "d", label: "Building architecture", status: "failed" },
        ]}
      />,
    );

    const region = screen.getByRole("status");
    expect(region).toHaveAttribute("aria-live", "polite");
    const items = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(items).toEqual([
      "Analyzing requirements: Done",
      "Calculating capacity: In progress",
      "Running validation: Pending",
      "Building architecture: Failed",
    ]);
  });
});

describe("ErrorBoundary", () => {
  function Bomb({ explode }: { explode: boolean }) {
    if (explode) throw new Error("boom");
    return <p>Simulation ready</p>;
  }

  function Harness() {
    const [explode, setExplode] = useState(true);
    return (
      <>
        <button onClick={() => setExplode(false)}>Fix</button>
        <ErrorBoundary label="Simulation">
          <Bomb explode={explode} />
        </ErrorBoundary>
      </>
    );
  }

  it("contains the failure and recovers on retry", async () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    render(<Harness />);

    expect(screen.getByRole("alert")).toHaveTextContent("Simulation could not be displayed.");

    await userEvent.click(screen.getByRole("button", { name: "Fix" }));
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(screen.getByText("Simulation ready")).toBeInTheDocument();
    spy.mockRestore();
  });
});

describe("ProvenanceTag", () => {
  it("labels where information came from in text, not colour alone", () => {
    render(
      <>
        <ProvenanceTag kind="ai" />
        <ProvenanceTag kind="calculated" label="Capacity engine" />
      </>,
    );
    expect(screen.getByText("AI proposal")).toBeInTheDocument();
    expect(screen.getByText("Capacity engine")).toBeInTheDocument();
  });
});
