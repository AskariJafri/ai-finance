import { ActivityIndicator, Text } from "react-native";

import { useBudgetRule } from "../api/hooks";
import { Card } from "./Card";
import { BudgetRuleBars } from "./BudgetRuleBars";
import { useTheme } from "../theme";

function currentMonthKey(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

/**
 * Replaces BalanceSplitCard on the Dashboard. That card split a *balance*
 * 50/30/20 and could never answer "what's left" — it always read exactly
 * 50/30/20 by construction. This one is fed by the real 50/30/20 budget report
 * (the same one Insights uses), titled explicitly by month so it doesn't read
 * as the same period as the rolling-30-day stats above it on the screen.
 */
export function BudgetRemainingCard() {
  const theme = useTheme();
  const month = currentMonthKey();
  const rule = useBudgetRule(month);

  if (rule.isLoading) {
    return (
      <Card>
        <ActivityIndicator color={theme.colors.primary} />
      </Card>
    );
  }
  if (!rule.data || rule.data.income <= 0) {
    return null;
  }

  const monthLabel = new Date(`${month}-01T00:00:00`).toLocaleDateString(undefined, {
    month: "long",
  });

  return (
    <Card>
      <Text style={{ color: theme.colors.text, fontSize: 16, fontWeight: "700", marginBottom: 12 }}>
        This month ({monthLabel})
      </Text>
      <BudgetRuleBars report={rule.data} />
    </Card>
  );
}
