import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CreateProjectDialog } from "@/features/projects/components/CreateProjectDialog";

const mutate = vi.fn();
const push = vi.fn();

vi.mock("@/hooks/use-projects", () => ({
  useCreateProject: () => ({ mutate, isPending: false, isError: false, error: null }),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn(), back: vi.fn(), prefetch: vi.fn() }),
}));

describe("CreateProjectDialog", () => {
  beforeEach(() => {
    mutate.mockReset();
    push.mockReset();
  });

  it("shows a validation error and does not submit an empty name", async () => {
    render(<CreateProjectDialog open onOpenChange={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Create system" }));

    const name = screen.getByLabelText("Name");
    expect(name).toHaveAttribute("aria-invalid", "true");
    expect(name).toHaveAccessibleDescription("Name is required");
    expect(mutate).not.toHaveBeenCalled();
  });

  it("submits valid input and navigates to requirements on success", async () => {
    const onOpenChange = vi.fn();
    mutate.mockImplementation((_input, options: { onSuccess: (p: { id: string; name: string }) => void }) =>
      options.onSuccess({ id: "prj_1", name: "Food delivery" }),
    );
    render(<CreateProjectDialog open onOpenChange={onOpenChange} />);

    await userEvent.type(screen.getByLabelText("Name"), "  Food delivery ");
    await userEvent.type(screen.getByLabelText("Description"), "Orders and couriers");
    await userEvent.click(screen.getByRole("button", { name: "Create system" }));

    expect(mutate).toHaveBeenCalledTimes(1);
    expect(mutate.mock.calls[0]?.[0]).toEqual({ name: "Food delivery", description: "Orders and couriers" });
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(push).toHaveBeenCalledWith("/project/prj_1/requirements");
  });
});
