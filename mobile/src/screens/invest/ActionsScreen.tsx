import { useNavigation } from "@react-navigation/native";
import { useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  AppState,
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
  useBrokerageConnections,
  useLatestActionRun,
  usePortfolio,
  useStartActionRun,
} from "../../api/hooks";
import type { FactorPlan, PortfolioAction, Sizing } from "../../api/types";
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
  const latest = useLatestActionRun();
  const start = useStartActionRun();
  const accept = useAcceptActionPlan();

  const linked = (connections.data ?? []).length > 0;
  const effectiveSizing: Sizing = sizing ?? (linked ? "dollars" : "shares");
  const parsed = Number(added.replace(/[^0-9.]/g, "")) || 0;

  // The plan lives on the server, not in this screen. Whatever the latest run
  // is — still building, finished, failed — is what we show, so leaving and
  // coming back (or switching apps) picks up exactly where it was.
  const runState = latest.data ?? null;
  const building = start.isPending || runState?.status === "running";
  const result = !building && runState?.status === "done" ? runState.result : null;
  const failed = !building && runState?.status === "failed" ? runState.error : null;

  // Start the form from the last plan's inputs, once, so "Build" asks the
  // same question again by default.
  const seeded = useRef(false);
  useEffect(() => {
    const req = runState?.request;
    if (seeded.current || !req) return;
    seeded.current = true;
    if (req.added_cash != null) setAdded(String(req.added_cash));
    if (req.max_new_positions != null) setNewNames(Math.min(3, req.max_new_positions));
    if (req.sizing) setSizing(req.sizing);
  }, [runState?.request]);

  // The start request can drop (app backgrounded, slow network) while the
  // run it started carries on server-side. Once a run from that attempt shows
  // up, the dropped request is not an error worth showing.
  useEffect(() => {
    if (!start.isError || !runState) return;
    if (new Date(runState.created_at).getTime() >= start.submittedAt - 5_000) start.reset();
  }, [runState?.id, runState?.status, start.isError]);

  // Timers pause while the app is in the background; check in the moment it
  // comes back rather than waiting for the next poll.
  useEffect(() => {
    const sub = AppState.addEventListener("change", (state) => {
      if (state === "active") latest.refetch();
    });
    return () => sub.remove();
  }, [latest]);

  const run = (force = false) =>
    start.mutate({
      added_cash: parsed,
      max_new_positions: newNames,
      sizing: effectiveSizing,
      force,
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
            onPress={() => run(false)}
            disabled={building}
            loading={building}
            style={{ marginTop: 14 }}
          />
          <Text style={[styles.hint, { color: theme.colors.textMuted }]}>
            Usually 1–3 minutes. It builds on the server, so feel free to leave
            this screen or switch apps — the plan will be here when you come
            back. Asking again today with the same inputs gives the same plan.
            Nothing is placed; you approve each trade yourself.
          </Text>
          {start.error && !building ? (
            <Text style={{ color: theme.colors.danger, fontSize: 12, marginTop: 8 }}>
              {(start.error as any)?.response?.data?.detail ??
                "Couldn't reach the server to start a plan."}
            </Text>
          ) : null}
          {failed ? (
            <Text style={{ color: theme.colors.danger, fontSize: 12, marginTop: 8 }}>
              {failed}
            </Text>
          ) : null}
        </Card>

        {building ? (
          <Card>
            <ActivityIndicator color={theme.colors.primary} />
            <Text style={[styles.hint, { color: theme.colors.textMuted, textAlign: "center" }]}>
              The committee is deliberating… You can leave this screen; the plan
              keeps building.
            </Text>
          </Card>
        ) : null}

        {result && runState ? (
          <>
            <Card>
              <Text style={[styles.cardTitle, { color: theme.colors.text }]}>
                {result.actions.length ? "The plan" : "No changes"}
              </Text>
              <Text style={{ color: theme.colors.textMuted, fontSize: 12, marginTop: 4 }}>
                {`Built ${new Date(runState.finished_at ?? runState.created_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })} for the ${runState.period} session`}
                {runState.reused ? " · same inputs as earlier today, so the same plan" : ""}
              </Text>
              <Text style={[styles.note, { color: theme.colors.textMuted }]}>{result.note}</Text>
              {result.source !== "ai" ? (
                <Text style={{ color: theme.colors.textMuted, fontSize: 12, marginTop: 8 }}>
                  {result.ai_note ??
                    (result.source === "rules"
                      ? "The model was unreachable — every verdict below is the rules-based read."
                      : "Some verdicts fell back to the rules-based read; each one says which it is.")}
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

            {result.factors ? <FactorCard factors={result.factors} /> : null}

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
                    <SignalLine score={h.signal_score} line={h.signal} />
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

            <Pressable onPress={() => run(true)} style={{ paddingVertical: 6 }}>
              <Text style={{ color: theme.colors.primary, fontSize: 13, fontWeight: "600" }}>
                Rebuild from scratch
              </Text>
              <Text style={{ color: theme.colors.textMuted, fontSize: 11, marginTop: 2 }}>
                Re-reads prices and news. The committee's verdicts are fixed for the
                trading day, so a rebuild only changes if your holdings or the market
                data did.
              </Text>
            </Pressable>

            <Text style={[styles.disclaimer, { color: theme.colors.textMuted }]}>
              {result.disclaimer}
            </Text>
          </>
        ) : null}
      </ScrollView>
    </SafeAreaView>
  );
}

/**
 * Where the plan's risk comes from — not how much of it there is.
 *
 * Weights already say what you own. This says what you are actually exposed
 * to, which is a different list: three tickers can be one bet, and a Canadian
 * holding US names carries a currency nobody chose. Breaches come first,
 * because a limit that changed the plan is the reason an order is smaller than
 * the committee's conviction would suggest.
 */
function FactorCard({ factors }: { factors: FactorPlan }) {
  const theme = useTheme();
  if (factors.reason) {
    return (
      <Card>
        <Text style={[styles.cardTitle, { color: theme.colors.text }]}>Risk factors</Text>
        <Text style={[styles.note, { color: theme.colors.textMuted }]}>{factors.reason}</Text>
      </Card>
    );
  }
  // Sorted by how much of the risk each one carries: the first line is the
  // answer to "what is this portfolio actually a bet on".
  const shown = [...factors.factors]
    .filter((f) => Math.abs(f.share_of_risk) >= 0.02 || Math.abs(f.projected_share_of_risk) >= 0.02)
    .sort((a, b) => Math.abs(b.share_of_risk) - Math.abs(a.share_of_risk));

  return (
    <Card>
      <Text style={[styles.cardTitle, { color: theme.colors.text }]}>Where the risk comes from</Text>

      {factors.breaches.map((b) => (
        <View
          key={b.key}
          style={[
            styles.breach,
            {
              borderLeftColor: b.binding ? theme.colors.negative : theme.colors.textMuted,
              backgroundColor: theme.colors.border,
            },
          ]}
        >
          <Text
            style={{
              color: b.binding ? theme.colors.negative : theme.colors.text,
              fontSize: 12,
              fontWeight: "700",
            }}
          >
            {b.binding ? `${b.name} limit — plan cut back` : `${b.name} — over the limit`}
          </Text>
          <Text style={{ color: theme.colors.textMuted, fontSize: 12, marginTop: 3 }}>
            {b.message}
          </Text>
        </View>
      ))}

      {shown.map((f) => {
        const moved = Math.abs(f.projected_exposure - f.exposure) >= 0.01;
        return (
          <View key={f.key} style={[styles.pick, { borderTopColor: theme.colors.border }]}>
            <View style={styles.pickHead}>
              <Text style={{ color: theme.colors.text, fontWeight: "600", fontSize: 14 }}>
                {f.name}
              </Text>
              <Text style={{ color: theme.colors.text, fontWeight: "700" }}>
                {f.exposure.toFixed(2)}
                {moved ? (
                  <Text style={{ color: theme.colors.textMuted, fontWeight: "400" }}>
                    {` → ${f.projected_exposure.toFixed(2)}`}
                  </Text>
                ) : null}
              </Text>
            </View>
            <Text style={{ color: theme.colors.textMuted, fontSize: 12, marginTop: 3 }}>
              {formatPercent(f.share_of_risk)} of risk
              {f.duration_years !== null ? ` · ${f.duration_years.toFixed(1)} yrs duration` : ""}
            </Text>
          </View>
        );
      })}

      {factors.idiosyncratic_share !== null ? (
        <View style={[styles.pick, { borderTopColor: theme.colors.border }]}>
          <View style={styles.pickHead}>
            <Text style={{ color: theme.colors.text, fontWeight: "600", fontSize: 14 }}>
              Single-name risk
            </Text>
            <Text style={{ color: theme.colors.text, fontWeight: "700" }}>
              {formatPercent(factors.idiosyncratic_share)}
            </Text>
          </View>
          <Text style={{ color: theme.colors.textMuted, fontSize: 12, marginTop: 3 }}>
            The part no factor explains — specific to the companies you hold.
          </Text>
        </View>
      ) : null}

      <Text style={[styles.hint, { color: theme.colors.textMuted }]}>
        Exposure of 1.00 means moving one-for-one with that factor. These limits
        can only ever make the app buy less; nothing here proposes a sale.
      </Text>
    </Card>
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
      <PriceStamp source={action.price_source} asOf={action.price_as_of} />
      <SignalLine score={action.signal_score} line={action.signal} />
      {action.reason ? (
        <Text style={{ color: theme.colors.textMuted, fontSize: 12, marginTop: 4 }}>
          {action.reason}
        </Text>
      ) : null}
    </View>
  );
}

/** Where an order's price came from and how fresh it is. */
function PriceStamp({ source, asOf }: { source?: string | null; asOf?: string | null }) {
  const theme = useTheme();
  if (!source && !asOf) return null;
  const when = asOf
    ? new Date(asOf).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" })
    : null;
  const broker = source?.startsWith("broker:");
  const label = broker
    ? `live from ${source!.slice("broker:".length)}`
    : source === "yahoo"
      ? "market data (TSX may lag ~15 min)"
      : source === "position value"
        ? "your position's current value"
        : source ?? "";
  return (
    <Text style={{ color: broker ? theme.colors.positive : theme.colors.textMuted, fontSize: 11, marginTop: 3 }}>
      {broker ? "● " : ""}Price {when ? `as of ${when}` : ""} · {label}
    </Text>
  );
}

/**
 * The trend/momentum score a verdict was anchored to. Shown so a plan reads
 * as "the numbers say X, the committee agreed / adjusted" rather than as an
 * opinion from nowhere.
 */
function SignalLine({ score, line }: { score?: number | null; line?: string | null }) {
  const theme = useTheme();
  if (score == null || !line) return null;
  const color =
    score >= 0.25 ? theme.colors.positive : score <= -0.25 ? theme.colors.negative : theme.colors.text;
  return (
    <Text style={{ color: theme.colors.textMuted, fontSize: 12, marginTop: 4 }}>
      <Text style={{ color, fontWeight: "700" }}>
        Signal {score >= 0 ? "+" : ""}
        {score.toFixed(2)}
      </Text>
      {"  "}
      {line.replace(/^trend score [+-]?[0-9.]+,?\s*/, "")}
    </Text>
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
  breach: { borderLeftWidth: 3, paddingLeft: 10, paddingVertical: 8, marginTop: 10 },
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
