import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { Checkbox } from "@/components/ui/checkbox";
import { RadioGroup } from "@/components/ui/radio-group";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";

describe("form primitives", () => {
  it("checkbox and switch are named by their visible label and toggle", () => {
    const onChecked = vi.fn();
    const onSwitch = vi.fn();
    render(
      <>
        <Checkbox label="Show ignored" onCheckedChange={onChecked} />
        <Switch label="Redis cache" onCheckedChange={onSwitch} />
      </>,
    );
    fireEvent.click(screen.getByText("Show ignored"));
    expect(onChecked).toHaveBeenCalledWith(true);
    expect(screen.getByRole("checkbox", { name: "Show ignored" })).toBeChecked();
    fireEvent.click(screen.getByRole("switch", { name: "Redis cache" }));
    expect(onSwitch).toHaveBeenCalledWith(true);
  });

  it("segmented radio group is a named radiogroup and selects on click", () => {
    function Harness() {
      const [value, setValue] = useState<"a" | "b">("a");
      return (
        <RadioGroup
          variant="segmented"
          label="Database"
          value={value}
          onValueChange={setValue}
          options={[
            { value: "a", label: "Primary only" },
            { value: "b", label: "+ Read replica" },
          ]}
        />
      );
    }
    render(<Harness />);
    expect(screen.getByRole("radiogroup", { name: "Database" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "+ Read replica" }));
    expect(screen.getByRole("radio", { name: "+ Read replica" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "Primary only" })).not.toBeChecked();
  });

  it("slider exposes value text and steps with the keyboard", () => {
    const onChange = vi.fn();
    render(
      <Slider
        aria-label="API replicas"
        aria-valuetext="3 replicas"
        min={1}
        max={5}
        value={3}
        onValueChange={onChange}
      />,
    );
    const thumb = screen.getByRole("slider", { name: "API replicas" });
    expect(thumb).toHaveAttribute("aria-valuetext", "3 replicas");
    fireEvent.keyDown(thumb, { key: "ArrowRight" });
    expect(onChange).toHaveBeenCalledWith(4);
  });
});
