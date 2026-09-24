import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { foodSecurity } from "@/api/mock/fixtures-analysis";
import { TooltipProvider } from "@/components/ui/tooltip";
import { SecurityView } from "@/features/security/components/SecurityView";

import { foodArchitecture, mutationResult, queryResult } from "./operate-test-utils";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/project/proj_food/security",
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/hooks/use-architecture", () => ({ useArchitecture: () => queryResult(foodArchitecture()) }));
vi.mock("@/hooks/use-security", () => ({
  useSecurity: () => queryResult(foodSecurity()),
  useRunSecurity: () => mutationResult(),
}));

describe("SecurityView", () => {
  it("shows Unknown with accessible text for controls the architecture does not specify", () => {
    render(
      <TooltipProvider>
        <SecurityView projectId="proj_food" />
      </TooltipProvider>,
    );

    const matrix = screen.getByRole("table", { name: "Security controls by component" });
    const kafkaRow = within(matrix).getByRole("row", { name: /^Kafka/ });
    // Kafka encryption at rest is null in the fixture.
    expect(kafkaRow).toHaveTextContent("Kafka encryption at rest is not specified in the architecture");
    expect(within(kafkaRow).getAllByText("Unknown").length).toBeGreaterThan(0);
    // Known values are icon + text, never colour alone.
    expect(kafkaRow).toHaveTextContent("Kafka authentication: Yes");
    expect(kafkaRow).toHaveTextContent("Kafka encryption in transit: No");

    expect(screen.getByText("93")).toBeInTheDocument();
    expect(screen.getAllByText("Information disclosure").length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: /View security overlay/ })).toHaveAttribute(
      "href",
      "/project/proj_food/architecture?mode=security",
    );
  });
});
