import { useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import {
  usePaperPortfolio,
  usePaperTrade,
  usePaperTrades,
} from "../../api/hooks";
import { Button } from "../../components/Button";
import { Card } from "../../components/Card";
import { alert } from "../../lib/alert";
import { useTheme } from "../../theme";
import { formatCurrency, formatPercent } from "../../utils/format";

export function PaperTradingScreen() {
  const theme = useTheme();
  const portfolio = usePaperPortfolio();
  const trades = usePaperTrades();
  const buy = usePaperTrade("buy");
  const sell = usePaperTrade("sell");
  const [symbol, setSymbol] = useState("");
  const [qty, setQty] = useState("");

  const act = (side: "buy" | "sell") => {
    const sym = symbol.trim().toUpperCase();
    const q = Number(qty);
    if (!sym || !(q > 0)) {
      alert("Missing info", "Enter a ticker and a share quantity.");
      return;
    }
    const m = side === "buy" ? buy : sell;
    m.mutate(
      { symbol: sym, qty: q },
      {
        onSuccess: () => setQty(""),
        onError: (err: any) =>
          alert("Trade failed", err?.response?.data?.detail ?? "Could not place trade."),
      },
    );
  };

  const acct = portfolio.data?.account;
  const pending = buy.isPending || sell.isPending;

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.colors.background }} edges={["top"]}>
      <ScrollView
        contentContainerStyle={{ padding: 16, gap: 14 }}
        refreshControl={
          <RefreshControl
            refreshing={portfolio.isRefetching}
            onRefresh={() => portfolio.refetch()}
            tintColor={theme.colors.primary}
          />
        }
      >
        <Text style={[styles.h1, { color: theme.colors.text }]}>Paper Trading</Text>

        <Card>
          <Text style={[styles.label, { color: theme.colors.textMuted }]}>
            Paper equity (CAD)
          </Text>
          <Text style={[styles.hero, { color: theme.colors.text }]}>
            {formatCurrency(acct?.equity ?? 0)}
          </Text>
          <View style={styles.statsRow}>
            <Stat label="Cash" value={formatCurrency(acct?.cash ?? 0)} color={theme.colors.text} />
            <Stat label="Invested" value={formatCurrency(acct?.invested ?? 0)} color={theme.colors.text} />
            <Stat
              label="Unrealized"
              value={`${(acct?.unrealized_pl ?? 0) >= 0 ? "+" : ""}${formatCurrency(acct?.unrealized_pl ?? 0)}`}
              color={(acct?.unrealized_pl ?? 0) >= 0 ? theme.colors.positive : theme.colors.negative}
            />
          </View>
        </Card>

        <Card>
          <Text style={[styles.cardTitle, { color: theme.colors.text }]}>Trade</Text>
          <View style={{ flexDirection: "row", gap: 10, marginTop: 10 }}>
            <TextInput
              value={symbol}
              onChangeText={setSymbol}
              autoCapitalize="characters"
              autoCorrect={false}
              placeholder="Ticker"
              placeholderTextColor={theme.colors.textMuted}
              style={[styles.input, { flex: 1.4, borderColor: theme.colors.border, backgroundColor: theme.colors.surfaceAlt, color: theme.colors.text, borderRadius: theme.radius.md }]}
            />
            <TextInput
              value={qty}
              onChangeText={setQty}
              keyboardType="decimal-pad"
              placeholder="Shares"
              placeholderTextColor={theme.colors.textMuted}
              style={[styles.input, { flex: 1, borderColor: theme.colors.border, backgroundColor: theme.colors.surfaceAlt, color: theme.colors.text, borderRadius: theme.radius.md }]}
            />
          </View>
          <View style={{ flexDirection: "row", gap: 10, marginTop: 10 }}>
            <Button title="Buy" onPress={() => act("buy")} loading={buy.isPending} style={{ flex: 1 }} />
            <Button title="Sell" variant="secondary" onPress={() => act("sell")} loading={sell.isPending} style={{ flex: 1 }} />
          </View>
        </Card>

        <Card>
          <Text style={[styles.cardTitle, { color: theme.colors.text }]}>Positions</Text>
          {portfolio.isLoading ? (
            <ActivityIndicator color={theme.colors.primary} style={{ marginTop: 12 }} />
          ) : (portfolio.data?.positions ?? []).length === 0 ? (
            <Text style={{ color: theme.colors.textMuted, marginTop: 10 }}>
              No paper positions yet. Buy something above — you start with $100,000
              of simulated cash.
            </Text>
          ) : (
            (portfolio.data?.positions ?? []).map((p) => (
              <View key={p.symbol} style={styles.posRow}>
                <View style={{ flex: 1 }}>
                  <Text style={{ color: theme.colors.text, fontWeight: "700" }}>
                    {p.symbol}
                    {p.opened_by === "autotrader" ? (
                      <Text style={{ color: theme.colors.primary, fontSize: 11 }}>  🤖 bot</Text>
                    ) : null}
                  </Text>
                  <Text style={{ color: theme.colors.textMuted, fontSize: 12 }}>
                    {p.qty} @ {formatCurrency(p.avg_price)} · now{" "}
                    {p.price != null ? formatCurrency(p.price) : "—"}
                  </Text>
                </View>
                <View style={{ alignItems: "flex-end" }}>
                  <Text style={{ color: theme.colors.text, fontWeight: "600" }}>
                    {formatCurrency(p.market_value)}
                  </Text>
                  <Text style={{ color: p.unrealized_pl >= 0 ? theme.colors.positive : theme.colors.negative, fontSize: 12 }}>
                    {p.unrealized_pl >= 0 ? "+" : ""}
                    {formatCurrency(p.unrealized_pl)} ({formatPercent(p.unrealized_pl_pct)})
                  </Text>
                </View>
              </View>
            ))
          )}
        </Card>

        {(trades.data ?? []).length > 0 ? (
          <Card>
            <Text style={[styles.cardTitle, { color: theme.colors.text }]}>Recent trades</Text>
            {(trades.data ?? []).slice(0, 10).map((t) => (
              <View key={t.id} style={styles.tradeRow}>
                <Text style={{ color: t.side === "buy" ? theme.colors.positive : theme.colors.negative, fontWeight: "700", width: 44 }}>
                  {t.side.toUpperCase()}
                </Text>
                <Text style={{ color: theme.colors.text, flex: 1 }}>
                  {t.qty} {t.symbol} @ {formatCurrency(t.price)}
                </Text>
                {t.source === "autotrader" ? (
                  <Text style={{ color: theme.colors.primary, fontSize: 11 }}>🤖</Text>
                ) : null}
              </View>
            ))}
          </Card>
        ) : null}

        <Text style={[styles.disclaimer, { color: theme.colors.textMuted }]}>
          {portfolio.data?.disclaimer}
        </Text>
      </ScrollView>
    </SafeAreaView>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color: string }) {
  const theme = useTheme();
  return (
    <View style={{ flex: 1 }}>
      <Text style={[styles.label, { color: theme.colors.textMuted }]}>{label}</Text>
      <Text style={{ color, fontSize: 15, fontWeight: "700", marginTop: 2 }}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  h1: { fontSize: 28, fontWeight: "800" },
  label: { fontSize: 12 },
  hero: { fontSize: 32, fontWeight: "800", marginTop: 4 },
  statsRow: { flexDirection: "row", marginTop: 16, gap: 12 },
  cardTitle: { fontSize: 16, fontWeight: "700" },
  input: { borderWidth: StyleSheet.hairlineWidth, paddingHorizontal: 12, height: 44, fontSize: 14 },
  posRow: { flexDirection: "row", alignItems: "center", marginTop: 12 },
  tradeRow: { flexDirection: "row", alignItems: "center", gap: 8, paddingVertical: 6 },
  disclaimer: { fontSize: 11, lineHeight: 15, textAlign: "center", paddingBottom: 8 },
});
