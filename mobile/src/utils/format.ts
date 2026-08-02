export function formatCurrency(amount: number, currency = "CAD"): string {
  try {
    return new Intl.NumberFormat("en-CA", {
      style: "currency",
      currency,
      maximumFractionDigits: 2,
    }).format(amount);
  } catch {
    return `$${amount.toFixed(2)}`;
  }
}

/** Transactions store outflow as positive; show it as a signed spend amount. */
export function formatSigned(amount: number, currency = "CAD"): string {
  const outflow = amount > 0;
  return `${outflow ? "-" : "+"}${formatCurrency(Math.abs(amount), currency)}`;
}

export function formatPercent(fraction: number): string {
  return `${Math.round(fraction * 100)}%`;
}

/**
 * "2026-07-02" -> "Jul 2", in the reader's own timezone.
 *
 * The parts are split out by hand rather than handed to `new Date(iso)`,
 * because a date-only string is spec'd to parse as *UTC* midnight while
 * `toLocaleDateString` renders in local time. Anywhere west of UTC that
 * subtracts a day: a payday of 2026-07-02 displayed as "Jul 1" in Toronto.
 * A transaction has no time of day to begin with, so local midnight is the
 * honest reading of it.
 */
export function formatDate(iso: string): string {
  const year = Number(iso.slice(0, 4));
  const mon = Number(iso.slice(5, 7));
  const day = Number(iso.slice(8, 10));
  return new Date(year, mon - 1, day).toLocaleDateString("en-CA", {
    month: "short",
    day: "numeric",
  });
}

/** "2026-07" -> "July 2026". */
export function formatMonth(month: string): string {
  const year = Number(month.slice(0, 4));
  const mon = Number(month.slice(5, 7));
  return new Date(year, mon - 1, 1).toLocaleDateString("en-CA", {
    month: "long",
    year: "numeric",
  });
}
