import { describe, expect, it } from "vitest";

import {
  formatBytes,
  formatCompact,
  formatCurrency,
  formatDateTime,
  formatNumber,
  formatPercent,
  formatRate,
  formatRelativeTime,
} from "@/lib/formatting";

describe("formatting", () => {
  it("formats compact numbers", () => {
    expect(formatCompact(8200)).toBe("8.2K");
    expect(formatCompact(2_400_000)).toBe("2.4M");
    expect(formatCompact(31_000)).toBe("31K");
    expect(formatCompact(950)).toBe("950");
    expect(formatCompact(null)).toBe("—");
  });

  it("formats plain numbers", () => {
    expect(formatNumber(1234567.891)).toBe("1,234,567.89");
    expect(formatNumber(2.5, 0)).toBe("3");
    expect(formatNumber(Number.NaN)).toBe("—");
  });

  it("formats percentages from ratios", () => {
    expect(formatPercent(0.82)).toBe("82%");
    expect(formatPercent(0.9995, 2)).toBe("99.95%");
    expect(formatPercent(undefined)).toBe("—");
  });

  it("formats currency", () => {
    expect(formatCurrency(247, "USD")).toBe("$247");
    expect(formatCurrency(12.5)).toBe("$12.50");
  });

  it("formats bytes", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(16 * 1024 ** 3)).toBe("16 GB");
    expect(formatBytes(-1)).toBe("—");
  });

  it("formats rates", () => {
    expect(formatRate(8200, "/s")).toBe("8.2K/s");
  });

  it("formats date-times", () => {
    expect(formatDateTime("2026-09-23T12:00:00.000Z")).toMatch(/Sep 2[23], 2026/);
    expect(formatDateTime("not a date")).toBe("—");
  });

  it("formats relative times", () => {
    const now = new Date("2026-09-23T12:00:00.000Z");
    expect(formatRelativeTime("2026-09-23T11:59:50.000Z", now)).toBe("just now");
    expect(formatRelativeTime("2026-09-23T11:55:00.000Z", now)).toBe("5 minutes ago");
    expect(formatRelativeTime("2026-09-23T09:00:00.000Z", now)).toBe("3 hours ago");
    expect(formatRelativeTime("2026-09-22T12:00:00.000Z", now)).toBe("yesterday");
    expect(formatRelativeTime("2026-09-23T14:00:00.000Z", now)).toBe("in 2 hours");
    expect(formatRelativeTime(null, now)).toBe("—");
  });
});
