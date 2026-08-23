import { useNavigation } from "@react-navigation/native";
import { useState } from "react";
import {
  ActivityIndicator,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import {
  useAutoTraderConfig,
  useAutoTraderRuns,
  useRunAutoTrader,
  useUpdateAutoTrader,
} from "../../api/hooks";
import { AutoTradeDecision, AutoTradeRun } from "../../api/types";
import { Button } from "../../components/Button";
import { Card } from "../../components/Card";
import { useTheme } from "../../theme";
import { formatCurrency } from "../../utils/format";

export function AutoTraderScreen() {
  const theme = useTheme();
  const navigation = useNavigation<any>();
  const config = useAutoTraderConfig();
  const update = useUpdateAutoTrader();
  const runMut = useRunAutoTrader();
  const runs = useAutoTraderRuns();
  const [lastRun, setLastRun] = useState<AutoTradeRun | null>(null);

  const cfg = config.data;
  const live = !!cfg?.enabled && !cfg?.dry_run;

  const actionColor = (a: string) =>
    a === "buy" ? theme.colors.positive : a === "sell" ? theme.colors.negative : theme.colors.textMuted;

  const decisions: AutoTradeDecision[] = lastRun?.decisions ?? [];

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.colors.background }} edges={["top"]}>
      <ScrollView contentContainerStyle={{ padding: 16, gap: 14 }}>
        <Text style={[styles.h1, { color: theme.colors.text }]}>Auto-Trader</Text>
        <Text style={[styles.sub, { color: theme.colors.textMuted }]}>
          A disciplined, rules-based scan of your watch list and everything you
          already hold. It writes suggestions into your trade checklist — it
          cannot place an order, and it does nothing at all until you enable it.
          {"\n\n"}Buys are capped at your per-trade size and your available
          cash; a sell suggestion only ever covers a position you actually hold.
        </Text>

        {config.isLoading || !cfg ? (
          <ActivityIndicator color={theme.colors.primary} style={{ marginTop: 20 }} />
        ) : (
          <>
            <Card>
              <View
                style={[
                  styles.statusPill,
                  { backgroundColor: live ? theme.colors.negative : theme.colors.surfaceAlt },
                ]}
              >
                <Text style={{ color: live ? "#fff" : theme.colors.textMuted, fontWeight: "700", fontSize: 12 }}>
                  {live
                    ? "ARMED — writes to your trade checklist"
                    : cfg.enabled
                      ? "DRY RUN — reports only"
                      : "DISABLED"}
                </Text>
              </View>

              <Row label="Enabled" hint="Master switch. Off = the bot never acts.">
                <Switch
                  value={cfg.enabled}
                  onValueChange={(v) => update.mutate({ enabled: v })}
                  trackColor={{ true: theme.colors.primary }}
                />
              </Row>
              <Row
                label="Dry run"
                hint="On = report what it found and stop there. Off = also add those suggestions to your trade checklist, where you approve or skip each one."
              >
                <Switch
                  value={cfg.dry_run}
                  onValueChange={(v) => update.mutate({ dry_run: v })}
                  trackColor={{ true: theme.colors.primary }}
                />
              </Row>

              <Text style={{ color: theme.colors.textMuted, fontSize: 13, marginTop: 12 }}>
                Sizing {formatCurrency(cfg.per_trade)} per trade · max {cfg.max_positions}{" "}
                positions{"\n"}Universe: {cfg.universe.join(", ")}
              </Text>

              <Button
                title={runMut.isPending ? "Scanning…" : "Run a scan now"}
                onPress={() => runMut.mutate(undefined, { onSuccess: (r) => setLastRun(r) })}
                loading={runMut.isPending}
                style={{ marginTop: 14 }}
              />
            </Card>

            {lastRun ? (
              <Card>
                <Text style={[styles.cardTitle, { color: theme.colors.text }]}>Latest scan</Text>
                {lastRun.executed > 0 ? (
                  <Button
                    title={`Review ${lastRun.executed} suggestion${lastRun.executed === 1 ? "" : "s"}`}
                    onPress={() => navigation.navigate("Rebalance")}
                    style={{ marginTop: 8, marginBottom: 10 }}
                  />
                ) : null}
                <Text style={{ color: theme.colors.textMuted, fontSize: 13, marginBottom: 4 }}>
                  {lastRun.summary}
                </Text>
                {decisions.map((d) => (
                  <View key={d.symbol} style={styles.decRow}>
                    <Text style={{ color: theme.colors.text, fontWeight: "700", width: 64 }}>
                      {d.symbol}
                    </Text>
                    <Text style={{ color: actionColor(d.action), fontWeight: "700", width: 46 }}>
                      {d.action}
                    </Text>
                    <Text style={{ color: theme.colors.textMuted, fontSize: 12, flex: 1 }}>
                      {d.reason}
                      {d.executed ? "  ✓" : ""}
                    </Text>
                  </View>
                ))}
              </Card>
            ) : null}

            {(runs.data ?? []).length > 0 ? (
              <Card>
                <Text style={[styles.cardTitle, { color: theme.colors.text }]}>Run history</Text>
                {(runs.data ?? []).slice(0, 8).map((r) => (
                  <View key={r.id} style={styles.histRow}>
                    <Text style={{ color: theme.colors.textMuted, fontSize: 12 }}>
                      {new Date(r.created_at).toLocaleString()}
                    </Text>
                    <Text style={{ color: theme.colors.text, fontSize: 12 }}>
                      {r.executed} suggestion{r.executed === 1 ? "" : "s"} ·{" "}
                      {r.dry_run ? "report only" : "checklist"}
                    </Text>
                  </View>
                ))}
              </Card>
            ) : null}

            <Text style={[styles.disclaimer, { color: theme.colors.textMuted }]}>
              Suggestions only — this bot cannot place an order. Every suggestion
              goes to your trade checklist and needs your approval. It does not
              beat the market; its value is consistency and risk control.
              Educational only.
            </Text>
          </>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

function Row({
  label,
  hint,
  children,
}: {
  label: string;
  hint: string;
  children: React.ReactNode;
}) {
  const theme = useTheme();
  return (
    <View style={styles.row}>
      <View style={{ flex: 1, paddingRight: 12 }}>
        <Text style={{ color: theme.colors.text, fontWeight: "600", fontSize: 15 }}>{label}</Text>
        <Text style={{ color: theme.colors.textMuted, fontSize: 12, marginTop: 2 }}>{hint}</Text>
      </View>
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  h1: { fontSize: 28, fontWeight: "800" },
  sub: { fontSize: 13, lineHeight: 19 },
  cardTitle: { fontSize: 16, fontWeight: "700", marginBottom: 4 },
  statusPill: {
    alignSelf: "flex-start",
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 12,
    marginBottom: 12,
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingVertical: 10,
  },
  decRow: { flexDirection: "row", alignItems: "flex-start", gap: 8, paddingVertical: 5 },
  histRow: { flexDirection: "row", justifyContent: "space-between", paddingVertical: 6 },
  disclaimer: { fontSize: 11, lineHeight: 15, textAlign: "center", paddingBottom: 8 },
});
