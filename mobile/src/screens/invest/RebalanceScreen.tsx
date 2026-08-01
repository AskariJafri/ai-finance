import { useEffect, useState } from "react";
import {
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import {
  useApproveAndSubmit,
  useBrokerageConnections,
  useExplainers,
  useMarkExecuted,
  usePortfolio,
  useProposeRebalance,
  useProposedOrders,
  useRefreshOrder,
  useRejectOrder,
  useSetTargets,
  useTargets,
} from "../../api/hooks";
import { Button } from "../../components/Button";
import { Card } from "../../components/Card";
import { ExplainerSheet } from "../../components/MetricTile";
import { ProposedOrderCard } from "../../components/ProposedOrderCard";
import { useTheme } from "../../theme";
import { formatCurrency } from "../../utils/format";

type Row = { symbol: string; pct: string };

export function RebalanceScreen() {
  const theme = useTheme();
  const portfolio = usePortfolio();
  const targets = useTargets();
  const setTargets = useSetTargets();
  const propose = useProposeRebalance();
  const proposed = useProposedOrders();
  const markExecuted = useMarkExecuted();
  const reject = useRejectOrder();
  const approve = useApproveAndSubmit();
  const refreshOrder = useRefreshOrder();
  const connections = useBrokerageConnections();
  // Live execution needs a linked account the broker granted trade access to.
  const tradingAccount = (connections.data ?? []).find(
    (c) => c.trading_enabled && c.status === "active",
  );
  const explainers = useExplainers().data ?? {};
  const [rows, setRows] = useState<Row[]>([]);
  const [showExplainer, setShowExplainer] = useState(false);

  // Seed the editor from saved targets, else current positions.
  useEffect(() => {
    if (rows.length > 0) return;
    if (targets.data && targets.data.length > 0) {
      setRows(targets.data.map((t) => ({ symbol: t.symbol, pct: String(Math.round(t.weight * 100)) })));
    } else if (portfolio.data?.positions.length) {
      setRows(
        portfolio.data.positions.map((p) => ({
          symbol: p.symbol,
          pct: String(Math.round(p.weight * 100)),
        })),
      );
    }
  }, [targets.data, portfolio.data]);

  const total = rows.reduce((sum, r) => sum + (Number(r.pct) || 0), 0);
  const valid = Math.abs(total - 100) < 0.5 && rows.every((r) => r.symbol.trim());

  const save = () =>
    setTargets.mutate(
      rows.map((r) => ({ symbol: r.symbol.trim().toUpperCase(), weight: (Number(r.pct) || 0) / 100 })),
    );

  const openOrders = (proposed.data ?? []).filter((o) => o.status === "proposed");
  const pastOrders = (proposed.data ?? []).filter((o) => o.status !== "proposed").slice(0, 10);

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.colors.background }} edges={["top"]}>
      <ScrollView contentContainerStyle={{ padding: 16, gap: 14 }}>
        <View style={styles.headerRow}>
          <Text style={[styles.h1, { color: theme.colors.text }]}>Rebalance</Text>
          <Pressable onPress={() => setShowExplainer(true)} hitSlop={8}>
            <Text style={{ color: theme.colors.primary, fontSize: 13 }}>What is this? ⓘ</Text>
          </Pressable>
        </View>

        <Card>
          <Text style={[styles.cardTitle, { color: theme.colors.text }]}>Target weights</Text>
          <Text style={{ color: theme.colors.textMuted, fontSize: 13, marginTop: 4 }}>
            The mix you want. Weights must sum to 100%; cash gets deployed toward
            these targets when you propose a rebalance.
          </Text>
          {rows.map((row, i) => (
            <View key={i} style={styles.targetRow}>
              <TextInput
                value={row.symbol}
                onChangeText={(v) =>
                  setRows((prev) => prev.map((r, j) => (j === i ? { ...r, symbol: v } : r)))
                }
                autoCapitalize="characters"
                placeholder="SYMBOL"
                placeholderTextColor={theme.colors.textMuted}
                style={[styles.symbolInput, fieldStyle(theme)]}
              />
              <TextInput
                value={row.pct}
                onChangeText={(v) =>
                  setRows((prev) => prev.map((r, j) => (j === i ? { ...r, pct: v } : r)))
                }
                keyboardType="number-pad"
                placeholderTextColor={theme.colors.textMuted}
                style={[styles.pctInput, fieldStyle(theme)]}
              />
              <Text style={{ color: theme.colors.textMuted }}>%</Text>
              <Pressable
                onPress={() => setRows((prev) => prev.filter((_, j) => j !== i))}
                hitSlop={8}
              >
                <Text style={{ color: theme.colors.danger, fontSize: 16 }}>✕</Text>
              </Pressable>
            </View>
          ))}
          <Pressable onPress={() => setRows((prev) => [...prev, { symbol: "", pct: "0" }])}>
            <Text style={{ color: theme.colors.primary, fontWeight: "600", marginTop: 10 }}>
              + Add symbol
            </Text>
          </Pressable>
          <Text
            style={{
              color: valid ? theme.colors.positive : theme.colors.danger,
              fontSize: 13,
              marginTop: 8,
              fontWeight: "600",
            }}
          >
            Total: {total}% {valid ? "✓" : "(must equal 100%)"}
          </Text>
          <Button
            title="Save targets"
            onPress={save}
            disabled={!valid}
            loading={setTargets.isPending}
            style={{ marginTop: 10 }}
          />
        </Card>

        <Card>
          <Text style={[styles.cardTitle, { color: theme.colors.text }]}>
            Propose a rebalance
          </Text>
          <Text style={{ color: theme.colors.textMuted, fontSize: 13, marginTop: 4 }}>
            The engine compares your actual weights to targets and proposes
            trades for anything drifted more than 5%. You place them at your own
            broker, then tap "I placed this" — your holdings update to match.
          </Text>
          <Button
            title="Propose orders"
            onPress={() => propose.mutate({})}
            loading={propose.isPending}
            style={{ marginTop: 12 }}
          />
          {propose.error ? (
            <Text style={{ color: theme.colors.danger, fontSize: 12, marginTop: 8 }}>
              {(propose.error as any)?.response?.data?.detail ?? "Proposal failed."}
            </Text>
          ) : null}
          {propose.data ? (
            <Text style={{ color: theme.colors.textMuted, fontSize: 12, marginTop: 8 }}>
              {propose.data.note}
              {propose.data.orders.length > 0
                ? ` Buys ${formatCurrency(propose.data.total_buy)} · sells ${formatCurrency(
                    propose.data.total_sell,
                  )}.`
                : ""}
            </Text>
          ) : null}
        </Card>

        {openOrders.length > 0 ? (
          <Card>
            <Text style={[styles.cardTitle, { color: theme.colors.text, marginBottom: 4 }]}>
              Your trade checklist
            </Text>
            <Text style={{ color: theme.colors.textMuted, fontSize: 12, marginBottom: 10 }}>
              {tradingAccount
                ? `Each order goes to ${tradingAccount.institution_name ?? "your broker"} only when you tap to confirm it. Sells first, then buys.`
                : "Place these at your broker, then mark each one done. Sells first, then buys."}
            </Text>
            <View style={{ gap: 10 }}>
              {openOrders.map((o) => (
                <ProposedOrderCard
                  key={o.id}
                  order={o}
                  onExecuted={(id, fill) =>
                    markExecuted.mutate({ orderId: id, fill_price: fill })
                  }
                  onReject={(id) => reject.mutate(id)}
                  onApprove={(id) => approve.mutate(id)}
                  onRefresh={(id) => refreshOrder.mutate(id)}
                  canTrade={tradingAccount != null}
                  brokerName={tradingAccount?.institution_name}
                  busy={
                    markExecuted.isPending ||
                    reject.isPending ||
                    approve.isPending ||
                    refreshOrder.isPending
                  }
                />
              ))}
            </View>
            {approve.error ? (
              <Text style={{ color: theme.colors.danger, fontSize: 12, marginTop: 8 }}>
                {(approve.error as any)?.response?.data?.detail ?? "Order failed."}
              </Text>
            ) : null}
          </Card>
        ) : null}

        {pastOrders.length > 0 ? (
          <Card>
            <Text style={[styles.cardTitle, { color: theme.colors.text, marginBottom: 8 }]}>
              Recent decisions
            </Text>
            {pastOrders.map((o) => (
              <View key={o.id} style={styles.historyRow}>
                <Text style={{ color: theme.colors.text, fontSize: 13 }}>
                  {o.side.toUpperCase()} {o.symbol}
                </Text>
                <Text style={{ color: theme.colors.textMuted, fontSize: 13 }}>
                  {formatCurrency(o.est_value)} · {o.status === "filled" ? "done" : o.status}
                </Text>
              </View>
            ))}
          </Card>
        ) : null}
      </ScrollView>

      {explainers.rebalancing ? (
        <ExplainerSheet
          explainer={explainers.rebalancing}
          visible={showExplainer}
          onClose={() => setShowExplainer(false)}
        />
      ) : null}
    </SafeAreaView>
  );
}

function fieldStyle(theme: ReturnType<typeof useTheme>) {
  return {
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: theme.colors.border,
    backgroundColor: theme.colors.surfaceAlt,
    borderRadius: theme.radius.sm,
    color: theme.colors.text,
    paddingHorizontal: 10,
    height: 40,
    fontSize: 14,
  } as const;
}

const styles = StyleSheet.create({
  headerRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  h1: { fontSize: 28, fontWeight: "800" },
  cardTitle: { fontSize: 16, fontWeight: "700" },
  targetRow: { flexDirection: "row", alignItems: "center", gap: 8, marginTop: 10 },
  symbolInput: { flex: 1 },
  pctInput: { width: 64, textAlign: "right" as const },
  historyRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingVertical: 6,
  },
});
