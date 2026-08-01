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

export function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString("en-CA", { month: "short", day: "numeric" });
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
