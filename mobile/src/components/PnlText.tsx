import { Text, TextStyle } from "react-native";

import { useTheme } from "../theme";
import { formatSignedCurrency, formatSignedPercent } from "../utils/format";

/**
 * One gain/loss figure — green up, red down, always signed.
 *
 * Holdings and the Invest home both show unrealized and day P/L, and getting
 * the sign and the colour to agree by hand in each place is how a loss ends up
 * painted green. `pct` is a fraction (0.0124 -> "+1.24%").
 */
export function PnlText({
  amount,
  pct,
  currency = "CAD",
  placeholder = "—",
  style,
}: {
  amount: number | null | undefined;
  pct?: number | null;
  currency?: string;
  /** Shown when the figure is unknown — a new position has no yesterday. */
  placeholder?: string;
  style?: TextStyle;
}) {
  const theme = useTheme();
  if (amount == null) {
    return <Text style={[{ color: theme.colors.textMuted }, style]}>{placeholder}</Text>;
  }
  return (
    <Text
      style={[
        { color: amount >= 0 ? theme.colors.positive : theme.colors.negative },
        style,
      ]}
    >
      {formatSignedCurrency(amount, currency)}
      {pct != null ? ` (${formatSignedPercent(pct)})` : ""}
    </Text>
  );
}
