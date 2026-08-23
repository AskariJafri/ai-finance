import { useNavigation } from "@react-navigation/native";
import { useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import {
  useAcceptActionPlan,
  useActionPlan,
  useBrokerageConnections,
  usePortfolio,
} from "../../api/hooks";
import type { PortfolioAction, Sizing } from "../../api/types";
import { Button } from "../../components/Button";
import { Card } from "../../components/Card";
import { useTheme } from "../../theme";
import { formatCurrency, formatPercent } from "../../utils/format";

const PRESETS = [0, 100, 500, 1000] as const;

export function ActionsScreen() {
  const theme = useTheme();
  const navigation = useNavigation<any>();
  const [added, setAdded] = useState("0");
  const [newNames, setNewNames] = useState(2);
  const [sizing, setSizing] = useState<Sizing | null>(null);

  const portfolio = usePortfolio();
  const connections = useBrokerageConnections();
  const plan = useActionPlan();
  const accept = useAcceptActionPlan();

  const linked = (connections.data ?? []).length > 0;
  const effectiveSizing: Sizing = sizing ?? (linked ? "dollars" : "shares");
  const parsed = Number(added.replace(/[^0-9.]/g, "")) || 0;
  const result = plan.data;

  const run = () =>
    plan.mutate({
      added_cash: parsed,
      max_new_positions: newNames,
      sizing: effectiveSizing,
    });

  const acceptPlan = () => {
    if (!result?.actions.length) return;
    accept.mutate(
      {
        orders: result.actions.map((a) => ({
          symbol: a.symbol,
          side: a.side,
          amount: a.amount,
          price: a.price,
          qty: a.qty,
          reason: a.reason,
        })),
        sizing: result.sizing,
      },
      { onSuccess: () => navigation.navigate("Rebalance") },
    );
  };

  const sells = result?.actions.filter((a) => a.side === "sell") ?? [];
  const buys = result?.actions.filter((a) => a.side === "buy") ?? [];

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.colors.background }} edges={["top"]}>
      <ScrollView contentContainerStyle={{ padding: 16, gap: 14 }}>
        <Text style={[styles.h1, { color: theme.colors.text }]}>What should I do?</Text>

        <Card>
          <Text style={[styles.body, { color: theme.colors.textMuted }]}>
            The committee judges every position you hold and the best of what you
            don't, then turns those verdicts into a single plan: what to sell,
            what to buy, and how much of each. Sells fund the buys.
          </Text>

          <Text style={[styles.label, { color: theme.colors.textMuted, marginTop: 16 }]}>
            Adding new cash? (CAD)
          </Text>
          <TextInput
            value={added}
            onChangeText={setAdded}
            keyboardType="decimal-pad"
            placeholder="0"
            placeholderTextColor={theme.colors.textMuted}
            style={[
              styles.input,
              {
                color: theme.colors.text,
                borderColor: theme.colors.border,
                backgroundColor: theme.colors.surfaceAlt,
                borderRadius: theme.radius.md,
              },
            ]}
          />
          <View style={styles.segmentRow}>
            {PRESETS.map((p) => (
              <Segment
                key={p}
                active={parsed === p}
                label={p === 0 ? "None" : formatCurrency(p)}
                onPress={() => setAdded(String(p))}
              />
            ))}
          </View>
          <Text style={[styles.hint, { color: theme.colors.textMuted }]}>
            On top of the {formatCurrency(portfolio.data?.account?.cash ?? 0)} already
            in the account.
          </Text>

          <Text style={[styles.label, { color: theme.colors.textMuted, marginTop: 16 }]}>
            New positions it may open
          </Text>
          <View style={styles.segmentRow}>
            {[0, 1, 2, 3].map((n) => (
              <Segment
                key={n}
                active={newNames === n}
                label={n === 0 ? "None" : `${n}`}
                onPress={() => setNewNames(n)}
              />
            ))}
          </View>

          <Text style={[styles.label, { color: theme.colors.textMuted, marginTop: 16 }]}>
            Size each trade in
          </Text>
          <View style={styles.segmentRow}>
            <Segment
              active={effectiveSizing === "dollars"}
              label="Dollar amounts"
              onPress={() => setSizing("dollars")}
            />
            <Segment
              active={effectiveSizing === "shares"}
              label="Whole shares"
              onPress={() => setSizing("shares")}
            />
          </View>

          <Button
            title="Build my plan"
            onPress={run}
            disabled={plan.isPending}
            loading={plan.isPending}
            style={{ marginTop: 14 }}
          />
          <Text style={[styles.hint, { color: theme.colors.textMuted }]}>
            One deliberation per holding and per candidate — usually 1–3 minutes,
            slower when the free model is busy. Keep this screen open. Nothing is
            placed; you approve each trade yourself.
          </Text>
          {plan.error ? (
            <Text style={{ color: theme.colors.danger, fontSize: 12, marginTop: 8 }}>
              {/* A timeout is not a failure to plan — it is the plan taking
                  longer than we waited. Saying "could not build a plan" sends
                  the user hunting for a problem with their portfolio. */}
              {(plan.error as any)?.code === "ECONNABORTED"
                ? "Timed out waiting for the committee. The free model is slow when it's busy — try again, or set 'New positions it may open' to None to halve the work."
                : ((plan.error as any)?.response?.data?.detail ??
                  "Couldn't reach the server to build a plan.")}
            </Text>
          ) : null}
        </Card>

        {plan.isPending ? (
          <Card>
            <ActivityIndicator color={theme.colors.primary} />
            <Text style={[styles.hint, { color: theme.colors.textMuted, textAlign: "center" }]}>
              The committee is deliberating… this can take a few minutes.
            </Text>
          </Card>
        ) : null}

        {result ? (
          <>
            <Card>
              <Text style={[styles.cardTitle, { color: theme.colors.text }]}>
                {result.actions.length ? "The plan" : "No changes"}
              </Text>
              <Text style={[styles.note, { color: theme.colors.textMuted }]}>{result.note}</Text>
              {result.source !== "ai" ? (
                <Text style={{ color: theme.colors.textMuted, fontSize: 12, marginTop: 8 }}>
                  {result.source === "rules"
                    ? "The model was unreachable — every verdict below is the rules-based read."
                    : "Some verdicts fell back to the rules-based read; each one says which it is."}
                </Text>
              ) : null}
            </Card>

            {sells.length ? (
              <Card>
                <Text style={[styles.cardTitle, { color: theme.colors.negative }]}>
                  Sell first
                </Text>
                {sells.map((a) => (
                  <ActionRow key={`${a.side}-${a.symbol}`} action={a} sizing={result.sizing} />
                ))}
              </Card>
            ) : null}

            {buys.length ? (
              <Card>
                <Text style={[styles.cardTitle, { color: theme.colors.positive }]}>
                  Then buy
                </Text>
                {buys.map((a) => (
                  <ActionRow key={`${a.side}-${a.symbol}`} action={a} sizing={result.sizing} />
                ))}
              </Card>
            ) : null}

            {result.holds.length ? (
              <Card>
                <Text style={[styles.cardTitle, { color: theme.colors.text }]}>
                  Leave alone
                </Text>
                {result.holds.map((h) => (
                  <View
                    key={h.symbol}
                    style={[styles.pick, { borderTopColor: theme.colors.border }]}
                  >
                    <View style={styles.pickHead}>
                      <Text style={[styles.symbol, { color: theme.colors.text }]}>{h.symbol}</Text>
                      <Text style={{ color: theme.colors.textMuted }}>
                        {formatPercent(h.weight)} of account
                      </Text>
                    </View>
                    <Text style={{ color: theme.colors.textMuted, fontSize: 12, marginTop: 4 }}>
                      {h.reason}
                    </Text>
                  </View>
                ))}
              </Card>
            ) : null}

            {result.actions.length ? (
              <Card>
                <Row label="Cash before" value={formatCurrency(result.cash_before)} />
                <Row label="Cash after" value={formatCurrency(result.cash_after)} />
                <Row label="Names considered" value={String(result.considered)} />
                <Button
                  title="Add to my trade checklist"
                  onPress={acceptPlan}
                  loading={accept.isPending}
                  style={{ marginTop: 12 }}
                />
                <Text style={[styles.hint, { color: theme.colors.textMuted }]}>
                  This only writes a checklist. Nothing is sent to your broker
                  until you approve each order.
                </Text>
              </Card>
            ) : null}

            <Text style={[styles.disclaimer, { color: theme.colors.textMuted }]}>
              {result.disclaimer}
            </Text>
          </>
        ) : null}
      </ScrollView>
    </SafeAreaView>
  );
}

function ActionRow({ action, sizing }: { action: PortfolioAction; sizing: Sizing }) {
  const theme = useTheme();
  const isSell = action.side === "sell";
  return (
    <View style={[styles.pick, { borderTopColor: theme.colors.border }]}>
      <View style={styles.pickHead}>
        <Text style={[styles.symbol, { color: theme.colors.text }]}>
          {action.symbol}
          <Text style={{ color: theme.colors.textMuted, fontWeight: "400", fontSize: 12 }}>
            {"  "}
            {action.action}
          </Text>
        </Text>
        <Text
          style={[
            styles.value,
            { color: isSell ? theme.colors.negative : theme.colors.positive },
          ]}
        >
          {isSell ? "−" : "+"}
          {formatCurrency(action.amount)}
        </Text>
      </View>
      <Text style={{ color: theme.colors.textMuted, fontSize: 13 }}>
        {/* Never print a bare `qty ?? 0`: a missing quantity next to a real
            dollar figure reads as "buy zero shares for $94". Derive it from the
            amount instead, which is always the number that was decided. */}
        {action.qty != null
          ? `${action.qty} share${action.qty === 1 ? "" : "s"} @ ${formatCurrency(action.price)}`
          : `≈ ${(action.amount / action.price).toFixed(4)} shares @ ${formatCurrency(action.price)}`}
        {"  ·  "}
        {/* Of the whole account, cash included — the Positions card on the hub
            weighs against invested value only, so this says which it means. */}
        {formatPercent(action.current_weight)} → {formatPercent(action.projected_weight)} of
        account
        {"  ·  conf "}
        {formatPercent(action.confidence)}
      </Text>
      {action.reason ? (
        <Text style={{ color: theme.colors.textMuted, fontSize: 12, marginTop: 4 }}>
          {action.reason}
        </Text>
      ) : null}
    </View>
  );
}

function Segment({
  active,
  label,
  onPress,
}: {
  active: boolean;
  label: string;
  onPress: () => void;
}) {
  const theme = useTheme();
  return (
    <Pressable
      onPress={onPress}
      style={{
        flex: 1,
        paddingVertical: 8,
        alignItems: "center",
        borderRadius: theme.radius.md,
        borderWidth: 1,
        borderColor: active ? theme.colors.primary : theme.colors.border,
        backgroundColor: active ? theme.colors.primary + "22" : "transparent",
      }}
    >
      <Text
        style={{
          color: active ? theme.colors.primary : theme.colors.textMuted,
          fontSize: 13,
          fontWeight: active ? "700" : "500",
        }}
      >
        {label}
      </Text>
    </Pressable>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  const theme = useTheme();
  return (
    <View style={styles.row}>
      <Text style={{ color: theme.colors.textMuted }}>{label}</Text>
      <Text style={{ color: theme.colors.text, fontWeight: "600" }}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  h1: { fontSize: 26, fontWeight: "800" },
  cardTitle: { fontSize: 16, fontWeight: "700" },
  label: { fontSize: 13, fontWeight: "600" },
  body: { fontSize: 14, lineHeight: 21 },
  note: { fontSize: 13, lineHeight: 20, marginTop: 6 },
  hint: { fontSize: 12, lineHeight: 17, marginTop: 8 },
  input: {
    borderWidth: 1,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 18,
    marginTop: 8,
  },
  segmentRow: { flexDirection: "row", gap: 8, marginTop: 10 },
  pick: { borderTopWidth: 1, paddingTop: 10, marginTop: 10 },
  pickHead: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  symbol: { fontSize: 15, fontWeight: "700" },
  value: { fontSize: 15, fontWeight: "700" },
  row: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingVertical: 6,
  },
  disclaimer: { fontSize: 11, lineHeight: 16 },
});
