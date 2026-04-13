/**
 * Shared formatting utilities for SavVio.
 *
 * Single source of truth for money, percentage, and numeric formatting
 * used across AiChat, FinancialDashboard, and TechnicalDetailsPanel.
 */

/** Format a number as USD without decimals. Returns "—" for null/NaN. */
export function fmtMoney(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return `$${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

/** Format a 0–1 ratio as "XX.X%". Returns "—" for null/NaN. */
export function fmtPct(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return `${(n * 100).toFixed(1)}%`;
}

/** Format a 0–1 ratio as "XX%" (no decimals). Returns "—" for null/NaN. */
export function fmtPctRounded(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return `${(n * 100).toFixed(0)}%`;
}

/** Format a number to fixed decimal places. Returns "—" for null/NaN. */
export function fmtNum(n: number | null | undefined, digits = 2): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toFixed(digits);
}

/** STIR = liquid savings / monthly income. Show as multiplier, not %. */
export function fmtStirMultiple(n: number): string {
  if (!Number.isFinite(n)) return "—";
  return `${n.toFixed(2)}×`;
}

/**
 * Classify a ratio against good/warn thresholds.
 * Used by FinancialDashboard health indicators and AiChat severity dots.
 */
export function ratioHealth(
  value: number | null | undefined,
  goodMax: number,
  warnMax: number,
): "good" | "warn" | "bad" | "unknown" {
  if (value == null || Number.isNaN(value)) return "unknown";
  if (value <= goodMax) return "good";
  if (value <= warnMax) return "warn";
  return "bad";
}
