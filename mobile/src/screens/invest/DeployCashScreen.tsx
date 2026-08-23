import { useEffect, useState } from "react";
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
import { useNavigation } from "@react-navigation/native";

import {
  useAcceptDeployPlan,
  useBrokerageConnections,
  useDeployPlan,
  usePortfolio,
} from "../../api/hooks";
import type { Sizing } from "../../api/types";
import { Button } from "../../components/Button";
import { Card } from "../../components/Card";
import { useTheme } from "../../theme";
import { formatCurrency, formatPercent } from "../../utils/format";

const PRESETS = [500, 1000, 5000] as const;
// How many names to split across. Fewer, larger positions is the right default
// on a small amount — whole shares mean a wide split just buys nothing.
const POSITION_CHOICES = [2, 3, 4, 5] as const;

export function DeployCashScreen() {
  const theme = useTheme();
  const navigation = useNavigation<any>();
  const [amount, setAmount] = useState("1000");
  const [positions, setPositions] = useState<number>(4);
  const [showPassed, setShowPassed] = useState(false);
  const [sizing, setSizing] = useState<Sizing | null>(null);

  const portfolio = usePortfolio();
  const connections = useBrokerageConnections();
  const plan = useDeployPlan();
  const accept = useAcceptDeployPlan();

  // A linked broker means fractional shares are buyable, and dollar sizing is
  // the only thing that works when one share costs more than the whole amount.
  // Whole shares stay the default without one, since that is what you can
  // actually place by hand. Once the user picks, their choice wins.
  const linked = (connections.data ?? []).length > 0;
  const effectiveSizing: Sizing = sizing ?? (linked ? "dollars" : "shares");

  const availableCash = portfolio.data?.account?.cash ?? 0;
  const parsed = Number(amount.replace(/[^0-9.]/g, "")) || 0;
  const result = plan.data;

  const run = () => {
    if (!(parsed > 0)) return;
    setShowPassed(false);
    plan.mutate({ amount: parsed, max_positions: positions, sizing: effectiveSizing });
  };

  const acceptPlan = () => {
    if (!result?.picks.length) return;
    accept.mutate(
      {
        orders: result.picks.map((p) => ({
          symbol: p.symbol,
          qty: p.qty,
          price: p.price,
          reason: p.rationale,
        })),
        sizing: result.sizing,
      },
      { onSuccess: () => navigation.navigate("Rebalance") },
    );
  };

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.colors.background }} edges={["top"]}>
      <ScrollView contentContainerStyle={{ padding: 16, gap: 14 }}>
        <Text style={[styles.h1, { color: theme.colors.text }]}>Invest cash</Text>

        <Card>
          <Text style={[styles.label, { color: theme.colors.textMuted }]}>
            How much do you want to invest? (CAD)
          </Text>
          <TextInput
            value={amount}
            onChangeText={setAmount}
            keyboardType="decimal-pad"
            placeholder="1000"
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
                label={formatCurrency(p)}
                onPress={() => setAmount(String(p))}
              />
            ))}
          </View>
          {availableCash > 0 ? (
            <Pressable onPress={() => setAmount(String(availableCash))} hitSlop={8}>
              <Text style={{ color: theme.colors.primary, fontSize: 12, marginTop: 10 }}>
                Use my cash — {formatCurrency(availableCash)}
              </Text>
            </Pressable>
          ) : null}

          <Text
            style={[styles.label, { color: theme.colors.textMuted, marginTop: 16 }]}
          >
            Split across up to
          </Text>
          <View style={styles.segmentRow}>
            {POSITION_CHOICES.map((n) => (
              <Segment
                key={n}
                active={positions === n}
                label={`${n} tickers`}
                onPress={() => setPositions(n)}
              />
            ))}
          </View>

          <Text style={[styles.label, { color: theme.colors.textMuted, marginTop: 16 }]}>
            Size each buy in
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
          <Text style={[styles.hint, { color: theme.colors.textMuted, marginTop: 6 }]}>
            {effectiveSizing === "dollars"
              ? "Spends the full amount and can buy a fraction of a share — Wealthsimple and Questrade both allow this."
              : "Whole shares only, so some of the amount stays in cash and pricier names may drop out."}
          </Text>

          <Button
            title="Build my plan"
            onPress={run}
            disabled={!(parsed > 0) || plan.isPending}
            loading={plan.isPending}
            style={{ marginTop: 14 }}
          />
          <Text style={[styles.hint, { color: theme.colors.textMuted }]}>
            A rules pass ranks the whole universe, then the AI committee argues over the
            shortlist — one deliberation per finalist. Give it a few seconds. You may get
            fewer names than asked for: the committee has to like them
            {effectiveSizing === "shares" ? ", and each position needs at least one whole share" : ""}.
          </Text>
          {plan.error ? (
            <Text style={{ color: theme.colors.danger, fontSize: 12, marginTop: 8 }}>
              {(plan.error as any)?.response?.data?.detail ?? "Could not build a plan."}
            </Text>
          ) : null}
        </Card>

        {plan.isPending ? <Working /> : null}

        {result ? (
          <>
            <Card>
              <Text style={[styles.cardTitle, { color: theme.colors.text }]}>
                {result.picks.length ? "Your plan" : "No buys this time"}
              </Text>
              <Text style={[styles.note, { color: theme.colors.textMuted }]}>{result.note}</Text>

              {result.picks.map((p) => (
                <View
                  key={p.symbol}
                  style={[styles.pick, { borderTopColor: theme.colors.border }]}
                >
                  <View style={styles.pickHead}>
                    <Text style={[styles.symbol, { color: theme.colors.text }]}>{p.symbol}</Text>
                    <Text style={[styles.value, { color: theme.colors.text }]}>
                      {formatCurrency(p.est_value)}
                    </Text>
                  </View>
                  <Text style={{ color: theme.colors.textMuted, fontSize: 13 }}>
                    {result.sizing === "dollars"
                      ? `≈ ${p.qty.toFixed(4)} shares @ ${formatCurrency(p.price)}`
                      : `${p.qty} share${p.qty === 1 ? "" : "s"} @ ${formatCurrency(p.price)}`}{" "}
                    · {formatPercent(p.weight)} of the amount · confidence{" "}
                    {formatPercent(p.confidence)}
                  </Text>
                  {p.rationale ? (
                    <Text style={{ color: theme.colors.textMuted, fontSize: 12, marginTop: 4 }}>
                      {p.rationale}
                    </Text>
                  ) : null}
                </View>
              ))}

              <View style={[styles.totals, { borderTopColor: theme.colors.border }]}>
                <Row label="Invested" value={formatCurrency(result.invested)} />
                <Row label="Stays in cash" value={formatCurrency(result.cash_remaining)} />
                <Row
                  label="Considered"
                  value={`${result.considered} ticker${result.considered === 1 ? "" : "s"} · ${
                    result.source === "ai" ? "AI committee" : "rules only (AI offline)"
                  }`}
                />
              </View>

              {result.picks.length ? (
                <>
                  <Button
                    title="Add to my trade checklist"
                    onPress={acceptPlan}
                    loading={accept.isPending}
                    style={{ marginTop: 14 }}
                  />
                  <Text style={[styles.hint, { color: theme.colors.textMuted }]}>
                    This only proposes the trades. You approve each one before anything is
                    placed.
                  </Text>
                </>
              ) : null}
              {accept.error ? (
                <Text style={{ color: theme.colors.danger, fontSize: 12, marginTop: 8 }}>
                  {(accept.error as any)?.response?.data?.detail ?? "Could not save the plan."}
                </Text>
              ) : null}
            </Card>

            {result.passed.length ? (
              <Card>
                <Pressable onPress={() => setShowPassed((s) => !s)} hitSlop={8}>
                  <Text style={[styles.cardTitle, { color: theme.colors.text }]}>
                    Considered and passed ({result.passed.length}) {showPassed ? "▾" : "▸"}
                  </Text>
                </Pressable>
                {showPassed
                  ? result.passed.map((p) => (
                      <View key={`${p.symbol}-${p.action}`} style={{ marginTop: 10 }}>
                        <Text style={{ color: theme.colors.text, fontSize: 14, fontWeight: "600" }}>
                          {p.symbol} — {p.action}
                        </Text>
                        <Text style={{ color: theme.colors.textMuted, fontSize: 12 }}>
                          {p.reason}
                        </Text>
                      </View>
                    ))
                  : null}
              </Card>
            ) : null}

            <Text style={[styles.meta, { color: theme.colors.textMuted }]}>
              {result.disclaimer}
            </Text>
          </>
        ) : !plan.isPending ? (
          <Text style={{ color: theme.colors.textMuted, textAlign: "center", padding: 12 }}>
            Enter an amount and the committee will turn it into a specific list of whole
            shares — with the reasoning for every name it picked, and every name it didn't.
          </Text>
        ) : null}
      </ScrollView>
    </SafeAreaView>
  );
}

/** A bare spinner reads as "stuck" over several minutes — name the stage instead. */
function Working() {
  const theme = useTheme();
  const [seconds, setSeconds] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, []);

  const stage =
    seconds < 10
      ? "Ranking candidates on trend and momentum…"
      : "Committee deliberating — five agents per ticker…";

  return (
    <Card>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
        <ActivityIndicator color={theme.colors.primary} />
        <View style={{ flex: 1 }}>
          <Text style={{ color: theme.colors.text, fontSize: 14, fontWeight: "600" }}>{stage}</Text>
          <Text style={{ color: theme.colors.textMuted, fontSize: 12, marginTop: 2 }}>
            {Math.floor(seconds / 60)}m {seconds % 60}s elapsed · usually 2–4 minutes
          </Text>
        </View>
      </View>
    </Card>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  const theme = useTheme();
  return (
    <View style={styles.row}>
      <Text style={{ color: theme.colors.textMuted, fontSize: 13 }}>{label}</Text>
      <Text style={{ color: theme.colors.text, fontSize: 13, fontWeight: "600" }}>{value}</Text>
    </View>
  );
}

function Segment({ active, label, onPress }: { active: boolean; label: string; onPress: () => void }) {
  const theme = useTheme();
  return (
    <Pressable
      onPress={onPress}
      style={[
        styles.segment,
        { backgroundColor: active ? theme.colors.primary : theme.colors.surfaceAlt },
      ]}
    >
      <Text
        style={{
          color: active ? theme.colors.primaryText : theme.colors.text,
          fontWeight: "600",
          fontSize: 13,
        }}
      >
        {label}
      </Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  h1: { fontSize: 28, fontWeight: "800" },
  cardTitle: { fontSize: 16, fontWeight: "700" },
  label: { fontSize: 13 },
  input: {
    marginTop: 8,
    borderWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 14,
    height: 44,
    fontSize: 15,
  },
  segmentRow: { flexDirection: "row", gap: 8, marginTop: 10 },
  segment: {
    flex: 1,
    height: 38,
    borderRadius: 8,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 4,
  },
  hint: { fontSize: 12, lineHeight: 17, marginTop: 8 },
  note: { fontSize: 13, lineHeight: 19, marginTop: 6 },
  pick: { borderTopWidth: StyleSheet.hairlineWidth, paddingTop: 10, marginTop: 12 },
  pickHead: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  symbol: { fontSize: 15, fontWeight: "700" },
  value: { fontSize: 15, fontWeight: "700" },
  totals: { borderTopWidth: StyleSheet.hairlineWidth, marginTop: 14, paddingTop: 10, gap: 6 },
  row: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  meta: { fontSize: 11, lineHeight: 15 },
});
