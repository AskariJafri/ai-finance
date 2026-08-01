import { Pressable, StyleSheet, Text, View } from "react-native";
import Svg, { Rect } from "react-native-svg";

import { MonthReport } from "../api/types";
import { useTheme } from "../theme";
import { formatCurrency } from "../utils/format";

// Chart series colors, CVD-validated per mode (theme greens/reds are fine for
// text/stats but the dark-mode pair is too light for chart marks).
const SERIES = {
  light: { income: "#16A34A", spending: "#DC2626" },
  dark: { income: "#059669", spending: "#EF4444" },
};

const CHART_HEIGHT = 140;
const BAR_WIDTH = 14;
const PAIR_GAP = 2; // surface gap between adjacent bars
const TOP_RADIUS = 4;

/** Paired income/spending bars per month; tap a month to select it. */
export function MonthlyBarsChart({
  months,
  selected,
  onSelect,
}: {
  months: MonthReport[];
  selected: string | null;
  onSelect: (month: string) => void;
}) {
  const theme = useTheme();
  const colors = theme.dark ? SERIES.dark : SERIES.light;
  const max = Math.max(...months.map((m) => Math.max(m.income, m.spending)), 1);
  const h = (v: number) => Math.max((v / max) * CHART_HEIGHT, v > 0 ? 3 : 0);

  return (
    <View>
      <View style={styles.chartRow}>
        {months.map((m) => {
          const isSelected = m.month === selected;
          const incomeH = h(m.income);
          const spendH = h(m.spending);
          const pairWidth = BAR_WIDTH * 2 + PAIR_GAP;
          return (
            <Pressable
              key={m.month}
              onPress={() => onSelect(m.month)}
              style={[
                styles.monthColumn,
                isSelected && {
                  backgroundColor: theme.colors.surfaceAlt,
                  borderRadius: theme.radius.sm,
                },
              ]}
            >
              <Svg width={pairWidth} height={CHART_HEIGHT}>
                <Rect
                  x={0}
                  y={CHART_HEIGHT - incomeH}
                  width={BAR_WIDTH}
                  height={incomeH}
                  rx={TOP_RADIUS}
                  fill={colors.income}
                  opacity={selected && !isSelected ? 0.45 : 1}
                />
                <Rect
                  x={BAR_WIDTH + PAIR_GAP}
                  y={CHART_HEIGHT - spendH}
                  width={BAR_WIDTH}
                  height={spendH}
                  rx={TOP_RADIUS}
                  fill={colors.spending}
                  opacity={selected && !isSelected ? 0.45 : 1}
                />
              </Svg>
              <Text
                style={[
                  styles.monthLabel,
                  {
                    color: isSelected ? theme.colors.text : theme.colors.textMuted,
                    fontWeight: isSelected ? "700" : "400",
                  },
                ]}
              >
                {monthAbbrev(m.month)}
              </Text>
            </Pressable>
          );
        })}
      </View>

      <View style={styles.legendRow}>
        <LegendItem color={colors.income} label="Income" />
        <LegendItem color={colors.spending} label="Spending" />
        {selected ? <SelectedValues months={months} selected={selected} /> : null}
      </View>
    </View>
  );
}

function SelectedValues({
  months,
  selected,
}: {
  months: MonthReport[];
  selected: string;
}) {
  const theme = useTheme();
  const m = months.find((x) => x.month === selected);
  if (!m) return null;
  return (
    <Text style={{ color: theme.colors.textMuted, fontSize: 12, marginLeft: "auto" }}>
      {formatCurrency(m.income)} in · {formatCurrency(m.spending)} out
    </Text>
  );
}

function LegendItem({ color, label }: { color: string; label: string }) {
  const theme = useTheme();
  return (
    <View style={styles.legendItem}>
      <View style={[styles.legendDot, { backgroundColor: color }]} />
      <Text style={{ color: theme.colors.textMuted, fontSize: 12 }}>{label}</Text>
    </View>
  );
}

function monthAbbrev(month: string): string {
  const year = Number(month.slice(0, 4));
  const mon = Number(month.slice(5, 7));
  return new Date(year, mon - 1, 1).toLocaleDateString("en-CA", { month: "short" });
}

const styles = StyleSheet.create({
  chartRow: {
    flexDirection: "row",
    justifyContent: "space-around",
    alignItems: "flex-end",
  },
  monthColumn: { alignItems: "center", paddingHorizontal: 6, paddingVertical: 4 },
  monthLabel: { fontSize: 12, marginTop: 4 },
  legendRow: { flexDirection: "row", gap: 14, marginTop: 10, alignItems: "center" },
  legendItem: { flexDirection: "row", alignItems: "center", gap: 5 },
  legendDot: { width: 9, height: 9, borderRadius: 5 },
});
