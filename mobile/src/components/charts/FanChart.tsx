import { StyleSheet, Text, View } from "react-native";
import Svg, { Line, Path } from "react-native-svg";

import { useTheme } from "../../theme";
import { formatCurrency } from "../../utils/format";

/**
 * Percentile fan: sequential single hue (theme primary) — bands are magnitude
 * around a median, not categories. Outer band (5-95) at 12% opacity, inner
 * (25-75) at 24%, solid 2px median, dashed start-value reference line. Band
 * edges are labeled directly, so no legend box is needed.
 */
export function FanChart({
  months,
  bands,
  startValue,
  height = 200,
  width = 300,
}: {
  months: number[];
  bands: { p5: number[]; p25: number[]; p50: number[]; p75: number[]; p95: number[] };
  startValue: number;
  height?: number;
  width?: number;
}) {
  const theme = useTheme();
  const n = months.length;
  // Need at least two points to draw a line/area; a single point makes
  // i/(n-1) => 0/0 => NaN, and a NaN in an SVG path hard-crashes the native
  // renderer (a full app close, not a catchable JS error).
  if (n < 2) return null;

  const finite = (v: number, fallback = 0) => (Number.isFinite(v) ? v : fallback);
  const start = finite(startValue);
  const p5 = (bands.p5 ?? []).map((v) => finite(v, start));
  const p95 = (bands.p95 ?? []).map((v) => finite(v, start));
  const min = Math.min(start, ...p5);
  const max = Math.max(start, ...p95);
  const span = max - min || 1;
  const pad = 6;
  const plotH = height - pad * 2;

  const x = (i: number) => finite((i / (n - 1)) * width);
  const y = (v: number) => finite(pad + plotH * (1 - (finite(v, start) - min) / span), pad);

  const area = (upper: number[], lower: number[]) => {
    const up = upper.map((v, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(v).toFixed(1)}`);
    const down = [...lower]
      .reverse()
      .map((v, i) => `L${x(n - 1 - i).toFixed(1)},${y(v).toFixed(1)}`);
    return [...up, ...down, "Z"].join(" ");
  };

  const median = bands.p50
    .map((v, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(v).toFixed(1)}`)
    .join(" ");

  return (
    <View>
      <View style={{ flexDirection: "row" }}>
        <Svg width={width} height={height}>
          <Path d={area(bands.p95, bands.p5)} fill={theme.colors.primary} opacity={0.12} />
          <Path d={area(bands.p75, bands.p25)} fill={theme.colors.primary} opacity={0.24} />
          <Path d={median} stroke={theme.colors.primary} strokeWidth={2} fill="none" />
          <Line
            x1={0}
            x2={width}
            y1={y(startValue)}
            y2={y(startValue)}
            stroke={theme.colors.textMuted}
            strokeWidth={1}
            strokeDasharray="4 4"
          />
        </Svg>
        <View style={styles.edgeLabels}>
          <Text style={[styles.edge, { color: theme.colors.textMuted }]}>
            95th {formatCurrency(bands.p95[n - 1] ?? 0)}
          </Text>
          <Text style={[styles.edge, { color: theme.colors.text, fontWeight: "700" }]}>
            median {formatCurrency(bands.p50[n - 1] ?? 0)}
          </Text>
          <Text style={[styles.edge, { color: theme.colors.textMuted }]}>
            5th {formatCurrency(bands.p5[n - 1] ?? 0)}
          </Text>
        </View>
      </View>
      <Text style={{ color: theme.colors.textMuted, fontSize: 11, marginTop: 6 }}>
        Dashed line = today's value ({formatCurrency(startValue)}). Shaded bands
        hold 50% (dark) and 90% (light) of simulated outcomes.
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  edgeLabels: { justifyContent: "space-around", paddingLeft: 6, width: 110 },
  edge: { fontSize: 11 },
});
