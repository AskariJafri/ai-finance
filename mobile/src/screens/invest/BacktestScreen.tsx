import { useState } from "react";
import {
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { useBacktest, useDcaPlan, useExplainers } from "../../api/hooks";
import { Button } from "../../components/Button";
import { Card } from "../../components/Card";
import { MetricTile } from "../../components/MetricTile";
import { LineChart } from "../../components/charts/LineChart";
import { useTheme } from "../../theme";
import { formatCurrency, formatPercent } from "../../utils/format";

const STRATEGIES = [
  { key: "buy_hold", label: "Buy & Hold" },
  { key: "dca", label: "DCA" },
  { key: "sma_crossover", label: "SMA 50/200" },
  { key: "fixed_mix", label: "60/40 mix" },
] as const;

export function BacktestScreen() {
  const theme = useTheme();
  const [strategy, setStrategy] = useState<(typeof STRATEGIES)[number]["key"]>("buy_hold");
  const [symbolsText, setSymbolsText] = useState("VFV.TO");
  const [start, setStart] = useState("2018-01-01");
  const [monthly, setMonthly] = useState("500");
  const backtest = useBacktest();
  const dcaPlan = useDcaPlan();
  const explainers = useExplainers().data ?? {};

  const symbols = symbolsText
    .split(/[,\s]+/)
    .map((s) => s.trim().toUpperCase())
    .filter(Boolean);

  const run = () => {
    backtest.mutate({
      strategy,
      symbols: strategy === "fixed_mix" && symbols.length < 2 ? ["SPY", "BND"] : symbols,
      start,
      monthly_amount: Number(monthly) || 500,
      weights: strategy === "fixed_mix" ? [0.6, 0.4] : undefined,
    });
    if (strategy === "dca") {
      const w = 1 / symbols.length;
      dcaPlan.mutate({
        monthly_amount: Number(monthly) || 500,
        allocations: symbols.map((s) => ({ symbol: s, weight: w })),
        start_date: start,
      });
    }
  };

  const result = backtest.data;
  const dca = strategy === "dca" ? dcaPlan.data : undefined;

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.colors.background }} edges={["top"]}>
      <ScrollView contentContainerStyle={{ padding: 16, gap: 14 }}>
        <Text style={[styles.h1, { color: theme.colors.text }]}>Backtest</Text>

        <Card>
          <View style={styles.segmentRow}>
            {STRATEGIES.map((s) => (
              <Pressable
                key={s.key}
                onPress={() => setStrategy(s.key)}
                style={[
                  styles.segment,
                  {
                    backgroundColor:
                      strategy === s.key ? theme.colors.primary : theme.colors.surfaceAlt,
                  },
                ]}
              >
                <Text
                  style={{
                    color: strategy === s.key ? theme.colors.primaryText : theme.colors.text,
                    fontWeight: "600",
                    fontSize: 12,
                  }}
                >
                  {s.label}
                </Text>
              </Pressable>
            ))}
          </View>

          <Field label="Symbols (comma-separated)" value={symbolsText} onChange={setSymbolsText} />
          <Field label="Start date (YYYY-MM-DD)" value={start} onChange={setStart} />
          {strategy === "dca" ? (
            <Field label="Monthly amount (USD)" value={monthly} onChange={setMonthly} numeric />
          ) : null}
          {result?.data_start ? (
            <Text style={{ color: theme.colors.textMuted, fontSize: 11, marginTop: 6 }}>
              Price history for these symbols starts {result.data_start} — earlier
              dates are clamped. Canadian tickers need “.TO” (e.g. VFV.TO).
            </Text>
          ) : null}
          <Button
            title="Run backtest"
            onPress={run}
            loading={backtest.isPending || dcaPlan.isPending}
            style={{ marginTop: 12 }}
          />
          {backtest.error ? (
            <Text style={{ color: theme.colors.danger, fontSize: 12, marginTop: 8 }}>
              {(backtest.error as any)?.response?.data?.detail ?? "Backtest failed — try a later start date or run a sync first."}
            </Text>
          ) : null}
        </Card>

        {result ? (
          <>
            <Card>
              <Text style={[styles.cardTitle, { color: theme.colors.text, marginBottom: 12 }]}>
                {STRATEGIES.find((s) => s.key === result.strategy)?.label} vs S&P 500
              </Text>
              <LineChart
                series={[
                  { label: "Strategy", points: result.equity_curve },
                  { label: "S&P 500 (SPY)", points: result.benchmark_curve },
                ]}
              />
            </Card>
            <View style={styles.grid}>
              <MetricTile
                label="CAGR"
                value={result.metrics.cagr != null ? formatPercent(result.metrics.cagr) : "—"}
                context={`Benchmark: ${
                  result.benchmark_metrics.cagr != null
                    ? formatPercent(result.benchmark_metrics.cagr)
                    : "—"
                }`}
                explainer={explainers.cagr}
              />
              <MetricTile
                label="Max drawdown"
                value={
                  result.metrics.max_drawdown != null
                    ? formatPercent(result.metrics.max_drawdown)
                    : "—"
                }
                context={`Benchmark: ${
                  result.benchmark_metrics.max_drawdown != null
                    ? formatPercent(result.benchmark_metrics.max_drawdown)
                    : "—"
                }`}
                explainer={explainers.max_drawdown}
              />
              <MetricTile
                label="Sharpe"
                value={result.metrics.sharpe != null ? result.metrics.sharpe.toFixed(2) : "—"}
                explainer={explainers.sharpe}
              />
              <MetricTile
                label="Trades"
                value={String(result.metrics.trades)}
                explainer={strategy === "sma_crossover" ? explainers.sma_crossover : explainers.dca}
              />
            </View>
          </>
        ) : null}

        {dca?.backtest ? (
          <Card>
            <Text style={[styles.cardTitle, { color: theme.colors.text, marginBottom: 8 }]}>
              DCA: value vs money in
            </Text>
            <LineChart
              series={[
                { label: "Portfolio value", points: dca.backtest.series },
                { label: "Contributed", points: dca.backtest.contributed_series },
              ]}
              height={150}
            />
            <Text style={{ color: theme.colors.text, fontSize: 14, marginTop: 10 }}>
              You'd have put in {formatCurrency(dca.backtest.contributed)} and
              ended with {formatCurrency(dca.backtest.final_value)}
              {dca.backtest.money_weighted_return != null
                ? ` — ${formatPercent(dca.backtest.money_weighted_return)}/yr money-weighted`
                : ""}
              .
            </Text>
            {dca.lump_sum_final_value != null ? (
              <Text style={{ color: theme.colors.textMuted, fontSize: 12, marginTop: 8 }}>
                Same total as a day-one lump sum: {formatCurrency(dca.lump_sum_final_value)}.{" "}
                {dca.lump_sum_note}
              </Text>
            ) : null}
          </Card>
        ) : null}

        {result ? (
          <Text style={[styles.meta, { color: theme.colors.textMuted }]}>
            No trading costs or taxes modeled; dividend-adjusted prices.{" "}
            {result.disclaimer}
          </Text>
        ) : null}
      </ScrollView>
    </SafeAreaView>
  );
}

function Field({
  label,
  value,
  onChange,
  numeric,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  numeric?: boolean;
}) {
  const theme = useTheme();
  return (
    <View style={{ marginTop: 10 }}>
      <Text style={{ fontSize: 13, color: theme.colors.textMuted }}>{label}</Text>
      <TextInput
        value={value}
        onChangeText={onChange}
        autoCapitalize="characters"
        keyboardType={numeric ? "decimal-pad" : "default"}
        placeholderTextColor={theme.colors.textMuted}
        style={{
          marginTop: 6,
          borderWidth: StyleSheet.hairlineWidth,
          borderColor: theme.colors.border,
          backgroundColor: theme.colors.surfaceAlt,
          borderRadius: theme.radius.md,
          color: theme.colors.text,
          paddingHorizontal: 12,
          height: 42,
          fontSize: 14,
        }}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  h1: { fontSize: 28, fontWeight: "800" },
  cardTitle: { fontSize: 16, fontWeight: "700" },
  segmentRow: { flexDirection: "row", gap: 6 },
  segment: {
    flex: 1,
    height: 38,
    borderRadius: 8,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 2,
  },
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
  meta: { fontSize: 11, lineHeight: 15 },
});
