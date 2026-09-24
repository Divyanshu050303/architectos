import { expect, type Page, test } from "@playwright/test";

/**
 * Performance acceptance (spec §64–65, §116) on the generated large project (proj_large,
 * ~120 components across 9 domains), production build with the mock backend.
 *
 * Budgets (documented in apps/web/README.md, "Performance budgets"), for a CI-class machine
 * (4 vCPU, Chromium, 1440×900):
 *   - time to interactive canvas  < 2 500 ms  (navigation start → nodes rendered and fitted)
 *   - frame time while panning    p95 < 50 ms (requestAnimationFrame deltas during a drag)
 *
 * PERF_BUDGET_SCALE multiplies both budgets for slower hardware (e.g. 1.5 on a busy laptop);
 * never lower the budgets to make a regression pass.
 */

const SCALE = Number(process.env.PERF_BUDGET_SCALE ?? "1") || 1;
const CANVAS_READY_BUDGET_MS = 2_500 * SCALE;
const PAN_FRAME_P95_BUDGET_MS = 50 * SCALE;

const LARGE = "/project/proj_large/architecture";

function percentile(values: readonly number[], p: number): number {
  const sorted = [...values].sort((a, b) => a - b);
  const index = Math.min(sorted.length - 1, Math.max(0, Math.ceil((p / 100) * sorted.length) - 1));
  return sorted[index] ?? Number.NaN;
}

/** Milliseconds from navigation start until the canvas shows nodes and has stopped fitting. */
async function timeToCanvas(page: Page, path: string): Promise<number> {
  await page.goto(path);
  const nodes = page.locator(".react-flow__node");
  await expect(nodes.first()).toBeVisible({ timeout: 20_000 });
  // "Interactive": the viewport transform has settled (fitView done) for two frames.
  return page.evaluate(
    () =>
      new Promise<number>((resolve) => {
        const viewport = document.querySelector(".react-flow__viewport");
        let last = viewport ? getComputedStyle(viewport).transform : "";
        let stable = 0;
        const tick = () => {
          const now = viewport ? getComputedStyle(viewport).transform : "";
          stable = now === last ? stable + 1 : 0;
          last = now;
          if (stable >= 2) resolve(performance.now());
          else requestAnimationFrame(tick);
        };
        requestAnimationFrame(tick);
      }),
  );
}

/** A point on the empty pane (not a node or edge) to start a drag from. */
async function emptyPanePoint(page: Page): Promise<{ x: number; y: number }> {
  const point = await page.getByLabel("Architecture canvas").evaluate((canvas) => {
    const box = canvas.getBoundingClientRect();
    for (let fy = 0.1; fy < 0.95; fy += 0.05) {
      for (let fx = 0.05; fx < 0.95; fx += 0.05) {
        const x = box.left + box.width * fx;
        const y = box.top + box.height * fy;
        const hit = document.elementFromPoint(x, y);
        if (
          hit?.closest(".react-flow__pane") &&
          !hit.closest(".react-flow__node, .react-flow__edge, button")
        ) {
          return { x, y };
        }
      }
    }
    return null;
  });
  if (!point) throw new Error("no empty canvas area to drag from");
  return point;
}

/** Frame deltas (ms) sampled with requestAnimationFrame while dragging the canvas. */
async function panFrameTimes(page: Page): Promise<number[]> {
  const start = await emptyPanePoint(page);
  await page.evaluate(() => {
    const w = window as Window & { __frames?: number[]; __sampling?: boolean };
    w.__frames = [];
    w.__sampling = true;
    let previous = performance.now();
    const sample = (now: number) => {
      w.__frames?.push(now - previous);
      previous = now;
      if (w.__sampling) requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
  });

  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  for (let i = 1; i <= 60; i++) {
    // Back and forth so the graph stays on screen.
    const dx = Math.sin((i / 60) * Math.PI * 2) * 300;
    const dy = Math.cos((i / 60) * Math.PI * 2) * 120 - 120;
    await page.mouse.move(start.x + dx, start.y + dy);
  }
  await page.mouse.up();

  return page.evaluate(() => {
    const w = window as Window & { __frames?: number[]; __sampling?: boolean };
    w.__sampling = false;
    // Drop the first delta: it spans the time before sampling started.
    return (w.__frames ?? []).slice(1);
  });
}

test.describe("performance: large architecture (proj_large)", () => {
  test.beforeEach(async ({ page }) => {
    // Warm the server and the JS chunks so the measurement is not a cold start.
    await page.goto("/project/proj_large");
    await page.waitForLoadState("networkidle");
  });

  for (const view of ["overview", "detailed"] as const) {
    test(`${view}: interactive canvas within ${CANVAS_READY_BUDGET_MS} ms`, async ({ page }) => {
      const ms = await timeToCanvas(page, `${LARGE}?view=${view}`);
      test.info().annotations.push({ type: "time-to-canvas", description: `${view}: ${Math.round(ms)} ms` });
      expect(await page.locator(".react-flow__node").count()).toBeGreaterThan(view === "overview" ? 3 : 60);
      expect(ms).toBeLessThan(CANVAS_READY_BUDGET_MS);
    });

    test(`${view}: pan frame time p95 under ${PAN_FRAME_P95_BUDGET_MS} ms`, async ({ page }) => {
      await timeToCanvas(page, `${LARGE}?view=${view}`);
      const frames = await panFrameTimes(page);
      expect(frames.length, "too few frames sampled").toBeGreaterThan(20);
      const p95 = percentile(frames, 95);
      test.info().annotations.push({
        type: "pan-frames",
        description: `${view}: ${frames.length} frames, p50 ${percentile(frames, 50).toFixed(1)} ms, p95 ${p95.toFixed(1)} ms`,
      });
      expect(p95).toBeLessThan(PAN_FRAME_P95_BUDGET_MS);
    });
  }
});
