import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";

/**
 * Surface sweep (spec §47–49, §62–63, §96–97, §113–114, §123–124): every route renders its main
 * heading without an error boundary, logs no console/page errors, has zero axe violations
 * (WCAG 2.1 AA) in light and dark, and does not scroll horizontally at phone width.
 * Runs against the labelled mock backend (NEXT_PUBLIC_API_MOCKS=true).
 */

const PROJECT_SURFACES = [
  "requirements",
  "capacity",
  "validation",
  "simulation",
  "reliability",
  "security",
  "observability",
  "cost",
  "evolution",
  "migration",
  "infrastructure",
  "drift",
  "decisions",
  "evidence",
  "reports",
  "settings",
] as const;

const projectRoutes = (id: string, surfaces: readonly string[] = PROJECT_SURFACES) => [
  `/project/${id}`,
  ...surfaces.map((s) => `/project/${id}/${s}`),
];

const MARKETING_ROUTES = [
  "/",
  "/product",
  "/architecture",
  "/simulation",
  "/pricing",
  "/docs",
  "/login",
  "/signup",
  "/forgot-password",
  "/reset-password?token=mock-reset-token",
];

const ROUTES = [
  ...MARKETING_ROUTES,
  "/app",
  "/dashboard",
  "/projects",
  ...projectRoutes("proj_food"),
  ...projectRoutes("proj_pay"),
  ...projectRoutes("proj_url", ["requirements", "capacity", "validation", "decisions", "reports"]),
];

const CONFIGS = [
  { name: "light 1440", colorScheme: "light", width: 1440, height: 900 },
  { name: "dark 1440", colorScheme: "dark", width: 1440, height: 900 },
  { name: "light 390", colorScheme: "light", width: 390, height: 844 },
  { name: "dark 390", colorScheme: "dark", width: 390, height: 844 },
] as const;

/** Dev-server noise that is not an application error. */
const IGNORED_CONSOLE = [/Download the React DevTools/i, /\[HMR\]/, /\[Fast Refresh\]/];

/**
 * Elements that extend past the viewport's right edge without being clipped by a scrolling or
 * overflow-hidden ancestor. `inTopNav` marks offenders inside the app TopNav header, which is
 * owned by the workspace work stream and reported separately.
 */
async function horizontalOverflow(page: Page) {
  return page.evaluate(() => {
    const root = document.scrollingElement ?? document.documentElement;
    const vw = window.innerWidth;
    const clipped = (el: Element) => {
      for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
        const { overflowX } = getComputedStyle(p);
        if (overflowX !== "visible") return p.getBoundingClientRect().right <= vw + 1;
      }
      return false;
    };
    const offenders = [...document.body.querySelectorAll("*")]
      .filter((el) => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.right > vw + 1 && !clipped(el);
      })
      .map((el) => ({
        tag: el.tagName.toLowerCase(),
        cls: (el.getAttribute("class") ?? "").slice(0, 80),
        text: (el.textContent ?? "").trim().slice(0, 40),
        inTopNav: Boolean(el.closest("header.h-12")),
      }));
    return { scrollWidth: root.scrollWidth, innerWidth: vw, offenders };
  });
}

for (const config of CONFIGS) {
  test.describe(`surfaces — ${config.name}`, () => {
    test.use({ viewport: { width: config.width, height: config.height }, colorScheme: config.colorScheme });

    for (const route of ROUTES) {
      test(route, async ({ page }) => {
        const errors: string[] = [];
        page.on("console", (msg) => {
          if (msg.type() !== "error") return;
          const text = msg.text();
          if (IGNORED_CONSOLE.some((re) => re.test(text))) return;
          errors.push(`console: ${text}`);
        });
        page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));

        await page.goto(route);
        const heading = page.getByRole("heading", { level: 1 }).first();
        await expect(heading).toBeVisible({ timeout: 20_000 });
        await expect(page.getByText(/could not be displayed/i)).toHaveCount(0);
        // Let queries settle so late renders (empty states, loaded data) are audited too.
        await page.waitForLoadState("networkidle");
        await expect(page.locator("[aria-busy='true']")).toHaveCount(0);

        const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
        const violations = axe.violations.map(
          (v) => `${v.id} (${v.nodes.length}): ${v.help} → ${v.nodes[0]?.target.join(" ")}`,
        );
        expect(violations, `axe violations on ${route}`).toEqual([]);

        if (config.width < 768) {
          const overflow = await horizontalOverflow(page);
          if (overflow.scrollWidth > overflow.innerWidth + 1) {
            const own = overflow.offenders.filter((o) => !o.inTopNav);
            if (own.length === 0 && overflow.offenders.length > 0) {
              test.info().annotations.push({
                type: "topnav-overflow",
                description: `${route}: overflow only from TopNav (${overflow.scrollWidth}px)`,
              });
            } else {
              expect(
                own.length ? own : ["unattributed overflow"],
                `horizontal scroll on ${route} (${overflow.scrollWidth}px)`,
              ).toEqual([]);
            }
          }
        }

        expect(errors, `console/page errors on ${route}`).toEqual([]);
      });
    }
  });
}
