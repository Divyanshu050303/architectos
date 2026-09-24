import { act, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import { PLAYBACK_STEP_MS, SimulationPlayback } from "@/features/architecture/components/SimulationOverlay";
import { buildPlaybackSteps } from "@/features/architecture/utils/simulation-playback";

const steps = buildPlaybackSteps([
  { atSeconds: 0, phase: "failure", nodeIds: ["postgres"], description: "PostgreSQL becomes unreachable." },
  { atSeconds: 15, phase: "dependency", nodeIds: ["order_service"], description: "Order Service loses it." },
  { atSeconds: 180, phase: "potential_failure", nodeIds: ["api"], description: "API may start failing." },
]);

function Harness({
  initial = 0,
  onChange,
  onPlayingChange,
}: {
  initial?: number;
  onChange?: (i: number) => void;
  onPlayingChange?: (playing: boolean) => void;
}) {
  const [index, setIndex] = useState(initial);
  return (
    <TooltipProvider>
      <SimulationPlayback
        steps={steps}
        index={index}
        onIndexChange={(i) => {
          onChange?.(i);
          setIndex(i);
        }}
        onPlayingChange={onPlayingChange}
      />
    </TooltipProvider>
  );
}

const status = () => screen.getByRole("status");

function mockReducedMotion(reduce: boolean) {
  vi.spyOn(window, "matchMedia").mockImplementation((query: string) => ({
    matches: reduce && query.includes("reduce"),
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }));
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("SimulationPlayback", () => {
  it("steps, scrubs and resets through the backend timeline", () => {
    render(<Harness />);
    expect(status()).toHaveTextContent("Normal");
    expect(screen.getByRole("button", { name: "Reset" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Step" }));
    expect(status()).toHaveTextContent("Failure");
    expect(screen.getByText("PostgreSQL becomes unreachable.")).toBeInTheDocument();
    expect(screen.getByText("Failure", { selector: "[aria-current='step']" })).toBeInTheDocument();

    // Scrub with the keyboard, like a user on the slider thumb: End jumps to the last step.
    fireEvent.keyDown(screen.getByRole("slider", { name: "Simulation timeline" }), { key: "End" });
    expect(status()).toHaveTextContent("Potential failure");
    expect(screen.getByRole("button", { name: "Step" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Reset" }));
    expect(status()).toHaveTextContent("Normal");
  });

  it("plays one step per tick, pauses, and stops at the end", () => {
    vi.useFakeTimers();
    mockReducedMotion(false);
    render(<Harness />);
    expect(screen.getByRole("region", { name: "Simulation playback" })).toHaveAttribute(
      "data-motion",
      "full",
    );

    fireEvent.click(screen.getByRole("button", { name: "Play" }));
    expect(screen.getByRole("button", { name: "Pause" })).toHaveAttribute("aria-pressed", "true");
    act(() => vi.advanceTimersByTime(PLAYBACK_STEP_MS));
    expect(status()).toHaveTextContent("Failure");

    fireEvent.click(screen.getByRole("button", { name: "Pause" }));
    act(() => vi.advanceTimersByTime(PLAYBACK_STEP_MS * 3));
    expect(status()).toHaveTextContent("Failure");

    fireEvent.click(screen.getByRole("button", { name: "Play" }));
    for (let i = 0; i < 5; i++) act(() => vi.advanceTimersByTime(PLAYBACK_STEP_MS));
    expect(status()).toHaveTextContent("Potential failure");
    expect(screen.getByRole("button", { name: "Play" })).toBeInTheDocument();
  });

  it("reports when playback runs, so the canvas can mark nodes simulating", () => {
    vi.useFakeTimers();
    const onPlayingChange = vi.fn();
    const { unmount } = render(<Harness onPlayingChange={onPlayingChange} />);
    expect(onPlayingChange).toHaveBeenLastCalledWith(false);
    fireEvent.click(screen.getByRole("button", { name: "Play" }));
    expect(onPlayingChange).toHaveBeenLastCalledWith(true);
    for (let i = 0; i < 4; i++) act(() => vi.advanceTimersByTime(PLAYBACK_STEP_MS));
    // Stops by itself at the last step.
    expect(onPlayingChange).toHaveBeenLastCalledWith(false);
    fireEvent.click(screen.getByRole("button", { name: "Play" }));
    expect(onPlayingChange).toHaveBeenLastCalledWith(true);
    unmount();
    expect(onPlayingChange).toHaveBeenLastCalledWith(false);
  });

  it("restarts from the beginning when played at the end", () => {
    const onChange = vi.fn();
    render(<Harness initial={3} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Play" }));
    expect(onChange).toHaveBeenCalledWith(0);
  });

  it("under reduced motion jumps between states without transitions", () => {
    vi.useFakeTimers();
    mockReducedMotion(true);
    render(<Harness />);
    const region = screen.getByRole("region", { name: "Simulation playback" });
    expect(region).toHaveAttribute("data-motion", "reduced");
    // Phase labels carry no transition classes.
    for (const phase of screen.getAllByRole("listitem")) {
      expect(phase.innerHTML).not.toContain("transition");
    }
    // Playback still advances: the state change stays understandable (spec §101).
    fireEvent.click(screen.getByRole("button", { name: "Play" }));
    act(() => vi.advanceTimersByTime(PLAYBACK_STEP_MS));
    expect(status()).toHaveTextContent("Failure");
  });
});
