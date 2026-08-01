import { useRoute } from "@react-navigation/native";
import { useState } from "react";
import {
  ActivityIndicator,
  Dimensions,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { useCandles } from "../../api/hooks";
import { Card } from "../../components/Card";
import { CandleChart } from "../../components/charts/CandleChart";
import { useTheme } from "../../theme";
import { formatCurrency, formatPercent } from "../../utils/format";

/** Preset -> (interval, range). The pairs the upstream feed actually serves:
 *  1-minute data only goes back about a week, so longer spans step the
 *  interval up rather than asking for something that comes back empty. */
const PRESETS = [
  { label: "1D", interval: "5m", range: "1d" },
  { label: "5D", interval: "15m", range: "5d" },
  { label: "1M", interval: "60m", range: "1mo" },
  { label: "6M", interval: "1d", range: "6mo" },
  { label: "1Y", interval: "1d", range: "1y" },
  { label: "5Y", interval: "1wk", range: "5y" },
] as const;

export function SymbolChartScreen() {
  const theme = useTheme();
  const route = useRoute<any>();
  const symbol: string = (route.params?.symbol ?? "").toUpperCase();
  const [presetIdx, setPresetIdx] = useState(0);
  const preset = PRESETS[presetIdx]!;

  const { data, isLoading, isError, error, refetch, isRefetching } = useCandles(
    symbol,
    preset.interval,
    preset.range,
  );

  const candles = data?.candles ?? [];
  const last = candles.length ? candles[candles.length - 1]! : null;
  // Change is measured against the previous close for an intraday view, and
  // against the first bar on screen for longer spans — otherwise a 5Y chart
  // would report the change since yesterday, which is not what it's showing.
  const baseline =
    preset.range === "1d"
      ? (data?.previous_close ?? candles[0]?.open ?? null)
      : (candles[0]?.open ?? null);
  const change = last && baseline ? last.close - baseline : null;
  const changePct = change != null && baseline ? change / baseline : null;
  const positive = (change ?? 0) >= 0;

  const chartWidth = Math.max(240, Dimensions.get("window").width - 64);

  return (
    <SafeAreaView
      style={{ flex: 1, backgroundColor: theme.colors.background }}
      edges={["bottom"]}
    >
      <ScrollView
        contentContainerStyle={{ padding: 16, gap: 14 }}
        refreshControl={
          <RefreshControl
            refreshing={isRefetching}
            onRefresh={refetch}
            tintColor={theme.colors.primary}
          />
        }
      >
        <Card>
          <Text style={[styles.symbol, { color: theme.colors.text }]}>{symbol}</Text>
          {data?.name ? (
            <Text style={{ color: theme.colors.textMuted, fontSize: 13 }}>{data.name}</Text>
          ) : null}

          {last ? (
            <View style={styles.priceRow}>
              <Text style={[styles.price, { color: theme.colors.text }]}>
                {formatCurrency(last.close, data?.currency ?? "USD")}
              </Text>
              {change != null && changePct != null ? (
                <Text
                  style={{
                    color: positive ? theme.colors.positive : theme.colors.negative,
                    fontSize: 15,
                    fontWeight: "700",
                  }}
                >
                  {positive ? "+" : "−"}
                  {formatCurrency(Math.abs(change), data?.currency ?? "USD")} (
                  {formatPercent(Math.abs(changePct))})
                </Text>
              ) : null}
            </View>
          ) : null}

          <View style={styles.chipRow}>
            {PRESETS.map((p, i) => (
              <Pressable
                key={p.label}
                onPress={() => setPresetIdx(i)}
                style={[
                  styles.chip,
                  {
                    backgroundColor:
                      i === presetIdx ? theme.colors.primary : theme.colors.surfaceAlt,
                    borderColor: i === presetIdx ? theme.colors.primary : theme.colors.border,
                  },
                ]}
              >
                <Text
                  style={{
                    color: i === presetIdx ? theme.colors.primaryText : theme.colors.text,
                    fontSize: 12,
                    fontWeight: "700",
                  }}
                >
                  {p.label}
                </Text>
              </Pressable>
            ))}
          </View>

          {isLoading ? (
            <View style={{ height: 220, justifyContent: "center" }}>
              <ActivityIndicator color={theme.colors.primary} />
            </View>
          ) : isError ? (
            <Text style={{ color: theme.colors.textMuted, marginTop: 12 }}>
              {(error as any)?.response?.data?.detail ??
                "Couldn't load this chart. Pull to retry."}
            </Text>
          ) : candles.length === 0 ? (
            <Text style={{ color: theme.colors.textMuted, marginTop: 12 }}>
              No price data for this range.
            </Text>
          ) : (
            <View style={{ marginTop: 12 }}>
              <CandleChart
                candles={candles}
                width={chartWidth}
                height={240}
                previousClose={preset.range === "1d" ? data?.previous_close : null}
              />
            </View>
          )}

          {last ? (
            <View style={styles.ohlcRow}>
              <Ohlc label="O" value={last.open} currency={data?.currency} />
              <Ohlc label="H" value={last.high} currency={data?.currency} />
              <Ohlc label="L" value={last.low} currency={data?.currency} />
              <Ohlc label="C" value={last.close} currency={data?.currency} />
            </View>
          ) : null}
        </Card>

        <Text style={{ color: theme.colors.textMuted, fontSize: 11, lineHeight: 16 }}>
          {data?.delayed
            ? "Prices are delayed, not live — this free market feed typically lags the exchange by around 15 minutes (crypto is close to real time). Updates about once a minute while this screen is open."
            : "Updates about once a minute while this screen is open."}
          {data?.market_state ? `  Market: ${data.market_state.toLowerCase()}.` : ""}
        </Text>
        {data?.disclaimer ? (
          <Text style={{ color: theme.colors.textMuted, fontSize: 11, lineHeight: 16 }}>
            {data.disclaimer}
          </Text>
        ) : null}
      </ScrollView>
    </SafeAreaView>
  );
}

function Ohlc({
  label,
  value,
  currency,
}: {
  label: string;
  value: number;
  currency?: string;
}) {
  const theme = useTheme();
  return (
    <View style={{ flex: 1 }}>
      <Text style={{ color: theme.colors.textMuted, fontSize: 11 }}>{label}</Text>
      <Text style={{ color: theme.colors.text, fontSize: 13, fontWeight: "600" }}>
        {formatCurrency(value, currency ?? "USD")}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  symbol: { fontSize: 22, fontWeight: "800" },
  priceRow: { flexDirection: "row", alignItems: "flex-end", gap: 10, marginTop: 8 },
  price: { fontSize: 28, fontWeight: "800" },
  chipRow: { flexDirection: "row", gap: 6, marginTop: 14, flexWrap: "wrap" },
  chip: {
    paddingHorizontal: 12,
    height: 30,
    borderRadius: 15,
    borderWidth: StyleSheet.hairlineWidth,
    alignItems: "center",
    justifyContent: "center",
  },
  ohlcRow: { flexDirection: "row", gap: 8, marginTop: 14 },
});
