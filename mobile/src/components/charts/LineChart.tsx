import { StyleSheet, Text, View } from "react-native";
import Svg, { Line, Path } from "react-native-svg";

import { SeriesPoint } from "../../api/types";
import { useTheme } from "../../theme";
import { formatCurrency } from "../../utils/format";

// CVD-validated series pair (dataviz validator): series 1 = theme primary,
// series 2 = amber #D97706 (passes on both light #FFF and dark #151B23).
export const CHART_SERIES2 = "#D97706";

type Series = { label: string; points: SeriesPoint[]; color?: string };

/** Generic multi-series (≤3) line chart: 2px paths, 3 recessive gridlines,
 * min/max y labels, legend row (identity never color-alone). */
export function LineChart({
  series,
  height = 180,
  width = 320,
}: {
  series: Series[];
  height?: number;
  width?: number;
}) {
  const theme = useTheme();
  const finite = (v: number, fallback = 0) => (Number.isFinite(v) ? v : fallback);
  // Only keep finite values — a NaN/Infinity reaching an SVG path crashes the
  // native renderer (a full app close, not a catchable JS error).
  const all = series.flatMap((s) => s.points.map((p) => p.value)).filter(Number.isFinite);
  if (all.length === 0) return null;
  const min = Math.min(...all);
  const max = Math.max(...all);
  const span = max - min || 1;
  const pad = 6;
  const plotH = height - pad * 2;

  const colors = [theme.colors.primary, CHART_SERIES2, theme.colors.textMuted];

  const toPath = (points: SeriesPoint[]) => {
    const n = points.length;
    if (n < 2) return "";
    return points
      .map((p, i) => {
        const x = finite((i / (n - 1)) * width);
        const y = finite(pad + plotH * (1 - (finite(p.value, min) - min) / span), pad);
        return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ");
  };

  return (
    <View>
      <View style={{ flexDirection: "row" }}>
        <Svg width={width} height={height}>
          {[0.25, 0.5, 0.75].map((f) => (
            <Line
              key={f}
              x1={0}
              x2={width}
              y1={pad + plotH * f}
              y2={pad + plotH * f}
              stroke={theme.colors.border}
              strokeWidth={StyleSheet.hairlineWidth}
            />
          ))}
          {series.map((s, i) => (
            <Path
              key={s.label}
              d={toPath(s.points)}
              stroke={s.color ?? colors[i % colors.length]}
              strokeWidth={2}
              fill="none"
            />
          ))}
        </Svg>
        <View style={styles.yLabels}>
          <Text style={[styles.yLabel, { color: theme.colors.textMuted }]}>
            {formatCurrency(max)}
          </Text>
          <Text style={[styles.yLabel, { color: theme.colors.textMuted }]}>
            {formatCurrency(min)}
          </Text>
        </View>
      </View>
      <View style={styles.legendRow}>
        {series.map((s, i) => (
          <View key={s.label} style={styles.legendItem}>
            <View
              style={[
                styles.legendDot,
                { backgroundColor: s.color ?? colors[i % colors.length] },
              ]}
            />
            <Text style={{ color: theme.colors.textMuted, fontSize: 12 }}>
              {s.label}
            </Text>
          </View>
        ))}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  yLabels: { justifyContent: "space-between", paddingLeft: 6, paddingVertical: 2 },
  yLabel: { fontSize: 10 },
  legendRow: { flexDirection: "row", gap: 14, marginTop: 8 },
  legendItem: { flexDirection: "row", alignItems: "center", gap: 5 },
  legendDot: { width: 9, height: 9, borderRadius: 5 },
});
