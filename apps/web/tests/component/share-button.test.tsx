import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ShareButton } from "@/components/navigation/TopNav";
import { Toaster } from "@/components/ui/toast";

vi.mock("@/hooks/use-projects", () => ({ useProject: () => ({ data: undefined, isPending: false }) }));

const DEEP_LINK = "/project/proj_food/architecture?node=postgres&mode=capacity";

function setClipboard(value: Clipboard | undefined) {
  Object.defineProperty(navigator, "clipboard", { value, configurable: true });
}

afterEach(() => {
  setClipboard(undefined);
  window.history.replaceState(null, "", "/");
});

describe("ShareButton", () => {
  it("copies the current deep link and confirms with a toast", async () => {
    window.history.replaceState(null, "", DEEP_LINK);
    const writeText = vi.fn().mockResolvedValue(undefined);
    setClipboard({ writeText } as unknown as Clipboard);
    render(
      <>
        <ShareButton />
        <Toaster />
      </>,
    );

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Share/ }));
    });

    expect(writeText).toHaveBeenCalledWith(`${window.location.origin}${DEEP_LINK}`);
    expect(await screen.findByText("Link copied")).toBeInTheDocument();
  });

  it("shows the link in a popover when the clipboard is unavailable", async () => {
    window.history.replaceState(null, "", DEEP_LINK);
    setClipboard(undefined);
    render(<ShareButton />);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Share/ }));
    });

    const input = await screen.findByRole("textbox", { name: "Link to this view" });
    expect(input).toHaveValue(`${window.location.origin}${DEEP_LINK}`);
    expect(input).toHaveAttribute("readonly");
  });

  it("falls back to the popover when writing to the clipboard is refused", async () => {
    setClipboard({ writeText: vi.fn().mockRejectedValue(new Error("denied")) } as unknown as Clipboard);
    render(<ShareButton />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Share/ }));
    });
    expect(await screen.findByRole("textbox", { name: "Link to this view" })).toBeInTheDocument();
  });
});
