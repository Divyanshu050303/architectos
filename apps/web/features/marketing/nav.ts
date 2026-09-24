export interface MarketingNavItem {
  label: string;
  href: "/product" | "/architecture" | "/simulation" | "/pricing" | "/docs";
}

/** Marketing routes (spec §117). The application lives under /app → /dashboard. */
export const MARKETING_NAV: readonly MarketingNavItem[] = [
  { label: "Product", href: "/product" },
  { label: "Architecture", href: "/architecture" },
  { label: "Simulation", href: "/simulation" },
  { label: "Pricing", href: "/pricing" },
  { label: "Docs", href: "/docs" },
];
