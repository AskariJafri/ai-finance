import { StyleSheet, Text, View } from "react-native";

import { BucketStat, BudgetRuleReport } from "../api/types";
import { useTheme } from "../theme";
import { formatCurrency, formatPercent } from "../utils/format";

/**
 * Three progress bars (needs/wants/savings) against the 50/30/20 targets.
 * The track represents 100% of income; a tick marks each bucket's target.
 */
export function BudgetRuleBars({ report }: { report: BudgetRuleReport }) {
  const theme = useTheme();

  if (report.income <= 0) {
    return (
      <Text style={{ color: theme.colors.textMuted }}>
        No income detected for this month, so the 50/30/20 split can't be
        computed. Set your monthly income manually if that's wrong.
      </Text>
    );
  }

  return (
    <View style={{ gap: 14 }}>
      <Row label="Needs" stat={report.needs} overIsBad />
      <Row label="Wants" stat={report.wants} overIsBad />
      <Row label="Savings" stat={report.savings} overIsBad={false} />
      {report.uncategorized_amount > 0 ? (
        <Text style={{ color: theme.colors.textMuted, fontSize: 12 }}>
          {formatCurrency(report.uncategorized_amount)} is uncategorized (counted
          as wants) — categorize those transactions for a more accurate split.
        </Text>
      ) : null}
    </View>
  );
}

// Needs/wants: remaining is headroom still available to spend this month.
// Savings: remaining reads the opposite way — a residual, not a spend budget —
// so a positive number means a shortfall against the goal, not room to spend.
function remainingLabel(label: string, remaining: number): string {
  if (label === "Savings") {
    return remaining > 0
      ? `${formatCurrency(remaining)} short of goal`
      : `${formatCurrency(-remaining)} ahead of goal`;
  }
  return remaining >= 0
    ? `${formatCurrency(remaining)} left this month`
    : `${formatCurrency(-remaining)} over`;
}

function Row({
  label,
  stat,
  overIsBad,
}: {
  label: string;
  stat: BucketStat;
  overIsBad: boolean;
}) {
  const theme = useTheme();
  const share = Math.max(stat.share, 0); // savings can be negative; clamp the bar
  const fillFrac = Math.min(share, 1);
  // For needs/wants the portion beyond target is flagged; for savings, meeting
  // or beating the target is the good case.
  const over = overIsBad && share > stat.target;
  const savingsOk = !overIsBad && share >= stat.target;
  const baseColor = overIsBad ? theme.colors.primary : theme.colors.positive;
  const remainingIsBad = label === "Savings" ? stat.remaining > 0 : stat.remaining < 0;

  return (
    <View>
      <View style={styles.labelRow}>
        <Text style={[styles.label, { color: theme.colors.text }]}>{label}</Text>
        <Text
          style={{
            color: remainingIsBad ? theme.colors.negative : theme.colors.positive,
            fontSize: 15,
            fontWeight: "700",
          }}
        >
          {remainingLabel(label, stat.remaining)}
        </Text>
      </View>
      <Text style={{ color: theme.colors.textMuted, fontSize: 12, marginBottom: 6 }}>
        {formatCurrency(stat.amount)} · {formatPercent(stat.share)} (target{" "}
        {formatPercent(stat.target)} · {formatCurrency(stat.target_amount)})
      </Text>
      <View style={[styles.track, { backgroundColor: theme.colors.surfaceAlt }]}>
        {/* Fill up to the target (or the full share if under target). */}
        <View
          style={[
            styles.fill,
            {
              width: `${Math.min(fillFrac, over ? stat.target : fillFrac) * 100}%`,
              backgroundColor: savingsOk ? theme.colors.positive : baseColor,
            },
          ]}
        />
        {/* Over-target overflow, tinted as a warning. */}
        {over ? (
          <View
            style={[
              styles.fill,
              {
                left: `${stat.target * 100}%`,
                width: `${(fillFrac - stat.target) * 100}%`,
                backgroundColor: theme.colors.negative,
              },
            ]}
          />
        ) : null}
        {/* Target tick. */}
        <View
          style={[
            styles.tick,
            { left: `${stat.target * 100}%`, backgroundColor: theme.colors.text },
          ]}
        />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  labelRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 6,
  },
  label: { fontSize: 14, fontWeight: "600" },
  track: {
    height: 10,
    borderRadius: 5,
    overflow: "hidden",
  },
  fill: {
    position: "absolute",
    top: 0,
    bottom: 0,
    left: 0,
    borderRadius: 5,
  },
  tick: {
    position: "absolute",
    top: -2,
    bottom: -2,
    width: 2,
    opacity: 0.55,
  },
});
