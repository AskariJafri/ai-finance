import { useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { useExplainers, useRiskMetrics } from "../../api/hooks";
import { Card } from "../../components/Card";
import { MetricTile } from "../../components/MetricTile";
import { CorrelationHeatmap } from "../../components/charts/CorrelationHeatmap";
import { useTheme } from "../../theme";
import { formatCurrency, formatPercent } from "../../utils/format";

const WINDOWS = ["1y", "3y", "max"] as const;

export function RiskScreen() {
  const theme = useTheme();
  const [window, setWindow] = useState<(typeof WINDOWS)[number]>("1y");
  const { data, isLoading } = useRiskMetrics(window);
  const explainers = useExplainers().data ?? {};

  const p = data?.portfolio;

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.colors.background }} edges={["top"]}>
      <Text style={[styles.h1, { color: theme.colors.text }]}>Risk</Text>

      <View style={styles.segmentRow}>
        {WINDOWS.map((w) => (
          <Pressable
            key={w}
            onPress={() => setWindow(w)}
            style={[
              styles.segment,
              {
                backgroundColor:
                  window === w ? theme.colors.primary : theme.colors.surfaceAlt,
              },
            ]}
          >
            <Text
              style={{
                color: window === w ? theme.colors.primaryText : theme.colors.text,
                fontWeight: "600",
              }}
            >
              {w}
            </Text>
          </Pressable>
        ))}
      </View>

      {isLoading ? (
        <View style={styles.center}>
          <ActivityIndicator color={theme.colors.primary} />
        </View>
      ) : !data || data.reason ? (
        <View style={styles.center}>
          <Text style={{ color: theme.colors.textMuted, textAlign: "center", padding: 24 }}>
            {data?.reason ??
        "Add holdings — or sync your linked brokerage — to see risk metrics."}
          </Text>
        </View>
      ) : (
        <ScrollView contentContainerStyle={{ padding: 16, gap: 14 }}>
          <View style={styles.grid}>
            <MetricTile
              label="Volatility"
              value={p?.ann_vol != null ? formatPercent(p.ann_vol) : "—"}
              context="Typical yearly swing size"
              explainer={explainers.ann_vol}
            />
            <MetricTile
              label="Sharpe"
              value={p?.sharpe != null ? p.sharpe.toFixed(2) : "—"}
              context="Return per unit of risk (≈1 is good)"
              explainer={explainers.sharpe}
            />
            <MetricTile
              label="Sortino"
              value={p?.sortino != null ? p.sortino.toFixed(2) : "—"}
              context="Punishes only downside swings"
              explainer={explainers.sortino}
            />
            <MetricTile
              label="Max drawdown"
              value={p?.max_drawdown != null ? formatPercent(p.max_drawdown) : "—"}
              context="Worst peak-to-trough fall"
              explainer={explainers.max_drawdown}
            />
            <MetricTile
              label="Beta vs S&P 500"
              value={p?.beta != null ? p.beta.toFixed(2) : "—"}
              context="1.0 = moves with the market"
              explainer={explainers.beta}
            />
            <MetricTile
              label="VaR 95% (1d)"
              value={p?.var_95_amount != null ? formatCurrency(p.var_95_amount) : "—"}
              context={
                p?.var_95_amount != null
                  ? "On 95% of days you would not lose more than this"
                  : undefined
              }
              explainer={explainers.var_95}
            />
            <MetricTile
              label="CVaR 95%"
              value={p?.cvar_95_amount != null ? formatCurrency(p.cvar_95_amount) : "—"}
              context="Average loss on the worst 5% of days"
              explainer={explainers.cvar_95}
            />
            <MetricTile
              label="Concentration"
              value={data.concentration ? data.concentration.hhi.toFixed(2) : "—"}
              context={
                data.concentration?.top_symbol
                  ? `Top: ${data.concentration.top_symbol} at ${formatPercent(
                      data.concentration.top_weight ?? 0,
                    )} · acts like ${data.concentration.effective_n} equal holdings`
                  : undefined
              }
              explainer={explainers.hhi}
            />
          </View>

          {data.correlation && data.correlation.symbols.length >= 2 ? (
            <Card>
              <Text style={[styles.cardTitle, { color: theme.colors.text, marginBottom: 12 }]}>
                Correlation — do your holdings move together?
              </Text>
              <CorrelationHeatmap
                symbols={data.correlation.symbols}
                matrix={data.correlation.matrix}
              />
              <Text style={{ color: theme.colors.textMuted, fontSize: 11, marginTop: 10 }}>
                Amber = move together, blue = move opposite. Above 0.8 everywhere
                means one bet in several names.
              </Text>
            </Card>
          ) : null}

          <Card>
            <Text style={[styles.cardTitle, { color: theme.colors.text }]}>Per position</Text>
            <View style={styles.tableHeader}>
              {["Symbol", "Weight", "Vol", "Beta", "MaxDD"].map((h) => (
                <Text key={h} style={[styles.th, { color: theme.colors.textMuted }]}>
                  {h}
                </Text>
              ))}
            </View>
            {data.per_position.map((row) => (
              <View key={row.symbol} style={styles.tableRow}>
                <Text style={[styles.td, { color: theme.colors.text, fontWeight: "700" }]}>
                  {row.symbol}
                </Text>
                <Text style={[styles.td, { color: theme.colors.text }]}>
                  {formatPercent(row.weight)}
                </Text>
                <Text style={[styles.td, { color: theme.colors.text }]}>
                  {row.ann_vol != null ? formatPercent(row.ann_vol) : "—"}
                </Text>
                <Text style={[styles.td, { color: theme.colors.text }]}>
                  {row.beta != null ? row.beta.toFixed(2) : "—"}
                </Text>
                <Text style={[styles.td, { color: theme.colors.text }]}>
                  {row.max_drawdown != null ? formatPercent(row.max_drawdown) : "—"}
                </Text>
              </View>
            ))}
          </Card>

          <Text style={[styles.meta, { color: theme.colors.textMuted }]}>
            Window: {data.data_quality.start} → {data.data_quality.end} (
            {data.data_quality.days_used} trading days) · risk-free rate assumption{" "}
            {formatPercent(data.risk_free_rate)}
          </Text>
          <Text style={[styles.meta, { color: theme.colors.textMuted }]}>{data.disclaimer}</Text>
        </ScrollView>
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  h1: { fontSize: 28, fontWeight: "800", paddingHorizontal: 16, paddingVertical: 12 },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  segmentRow: { flexDirection: "row", gap: 8, paddingHorizontal: 16, marginBottom: 4 },
  segment: {
    flex: 1,
    height: 36,
    borderRadius: 8,
    alignItems: "center",
    justifyContent: "center",
  },
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
  cardTitle: { fontSize: 16, fontWeight: "700" },
  tableHeader: { flexDirection: "row", marginTop: 12, paddingBottom: 6 },
  th: { flex: 1, fontSize: 11, fontWeight: "600" },
  tableRow: { flexDirection: "row", paddingVertical: 6 },
  td: { flex: 1, fontSize: 13 },
  meta: { fontSize: 11, lineHeight: 15 },
});
