import { StyleSheet, Text, View } from "react-native";

import { useTheme } from "../../theme";

/**
 * n×n correlation grid. Diverging encoding — cool (blue) for negative, warm
 * (orange) for positive, NEUTRAL GRAY at zero — and every cell prints its
 * value, so color is never the only encoding.
 */
export function CorrelationHeatmap({
  symbols,
  matrix,
}: {
  symbols: string[];
  matrix: number[][];
}) {
  const theme = useTheme();
  const cell = Math.min(64, 260 / Math.max(1, symbols.length));

  const colorFor = (v: number) => {
    // Blend from gray (0) toward blue (-1) / orange (+1).
    const t = Math.min(1, Math.abs(v));
    const alpha = 0.12 + 0.5 * t;
    return v >= 0
      ? `rgba(217, 119, 6, ${alpha.toFixed(2)})` // warm (amber)
      : `rgba(37, 99, 235, ${alpha.toFixed(2)})`; // cool (blue)
  };

  return (
    <View>
      <View style={{ flexDirection: "row", marginLeft: 44 }}>
        {symbols.map((s) => (
          <Text
            key={s}
            style={[styles.axis, { width: cell, color: theme.colors.textMuted }]}
            numberOfLines={1}
          >
            {s}
          </Text>
        ))}
      </View>
      {matrix.map((row, i) => (
        <View key={symbols[i]} style={{ flexDirection: "row", alignItems: "center" }}>
          <Text
            style={[styles.axis, { width: 44, color: theme.colors.textMuted }]}
            numberOfLines={1}
          >
            {symbols[i]}
          </Text>
          {row.map((v, j) => (
            <View
              key={`${i}-${j}`}
              style={[
                styles.cell,
                {
                  width: cell,
                  height: cell * 0.7,
                  backgroundColor: colorFor(v),
                  borderColor: theme.colors.background,
                },
              ]}
            >
              <Text style={[styles.value, { color: theme.colors.text }]}>
                {v.toFixed(2)}
              </Text>
            </View>
          ))}
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  axis: { fontSize: 11, textAlign: "center" },
  cell: {
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 1,
  },
  value: { fontSize: 10, fontWeight: "600" },
});
