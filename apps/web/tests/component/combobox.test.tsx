import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { Combobox, type ComboboxOption, filterComboboxOptions } from "@/components/ui/combobox";

const OPTIONS: ComboboxOption[] = [
  { value: "pg", label: "PostgreSQL failure", description: "Primary becomes unreachable" },
  { value: "redis", label: "Redis failure", description: "Cache restarts empty" },
  { value: "kafka", label: "Kafka outage", description: "Stops accepting writes", disabled: true },
  { value: "region", label: "Region failure", description: "Everything in the region goes down" },
];

function Harness({ onChange = vi.fn() }: { onChange?: (value: string) => void }) {
  const [value, setValue] = useState("pg");
  return (
    <>
      <label htmlFor="scenario">Scenario</label>
      <Combobox
        id="scenario"
        options={OPTIONS}
        value={value}
        listLabel="Scenarios"
        onValueChange={(next) => {
          setValue(next);
          onChange(next);
        }}
      />
    </>
  );
}

describe("Combobox", () => {
  it("is a labelled combobox that controls a listbox", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const input = screen.getByRole("combobox", { name: "Scenario" });
    expect(input).toHaveValue("PostgreSQL failure");
    expect(input).toHaveAttribute("aria-expanded", "false");

    await user.click(input);
    expect(input).toHaveAttribute("aria-expanded", "true");
    const listbox = screen.getByRole("listbox", { name: "Scenarios" });
    expect(input).toHaveAttribute("aria-controls", listbox.id);
    expect(screen.getAllByRole("option")).toHaveLength(4);
    expect(screen.getByRole("option", { name: /PostgreSQL/ })).toHaveAttribute("aria-selected", "true");
  });

  it("filters as you type and selects with the keyboard via aria-activedescendant", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    const input = screen.getByRole("combobox");

    await user.click(input);
    await user.clear(input);
    await user.type(input, "failure");
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual([
      expect.stringContaining("PostgreSQL"),
      expect.stringContaining("Redis"),
      expect.stringContaining("Region"),
    ]);
    expect(input).toHaveAttribute("aria-activedescendant", screen.getAllByRole("option")[0]?.id);

    await user.keyboard("{ArrowDown}");
    const redis = screen.getByRole("option", { name: /Redis/ });
    expect(input).toHaveAttribute("aria-activedescendant", redis.id);
    await user.keyboard("{Enter}");
    expect(onChange).toHaveBeenCalledWith("redis");
    expect(input).toHaveValue("Redis failure");
    expect(input).toHaveAttribute("aria-expanded", "false");
  });

  it("wraps, jumps with Home/End, skips nothing but never selects a disabled option", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    const input = screen.getByRole("combobox");
    input.focus();

    await user.keyboard("{ArrowDown}"); // opens on the selected option
    expect(input).toHaveAttribute("aria-activedescendant", screen.getAllByRole("option")[0]?.id);
    await user.keyboard("{ArrowUp}"); // wraps to the last
    expect(input).toHaveAttribute("aria-activedescendant", screen.getAllByRole("option")[3]?.id);
    await user.keyboard("{Home}{ArrowDown}{ArrowDown}"); // Kafka (disabled)
    expect(screen.getByRole("option", { name: /Kafka/ })).toHaveAttribute("aria-disabled", "true");
    await user.keyboard("{Enter}");
    expect(onChange).not.toHaveBeenCalled();
    await user.keyboard("{End}{Enter}");
    expect(onChange).toHaveBeenCalledWith("region");
  });

  it("closes on Escape and restores the selected label", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const input = screen.getByRole("combobox");
    await user.click(input);
    await user.clear(input);
    await user.type(input, "zzz");
    expect(screen.getByText("No matches.")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(input).toHaveAttribute("aria-expanded", "false");
    expect(input).toHaveValue("PostgreSQL failure");
  });

  it("selects with the mouse", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    await user.click(screen.getByRole("combobox"));
    await user.click(screen.getByRole("option", { name: /Region/ }));
    expect(onChange).toHaveBeenCalledWith("region");
  });

  it("matches every term against label and description", () => {
    expect(filterComboboxOptions(OPTIONS, "cache empty").map((o) => o.value)).toEqual(["redis"]);
    expect(filterComboboxOptions(OPTIONS, "  ")).toHaveLength(4);
  });
});
