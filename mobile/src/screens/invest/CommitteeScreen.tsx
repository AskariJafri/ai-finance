import { useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { useAnalyzeStock, useCommitteeSessions } from "../../api/hooks";
import { AgentTake, CommitteeResult } from "../../api/types";
import { Button } from "../../components/Button";
import { Card } from "../../components/Card";
import { useTheme } from "../../theme";

export function CommitteeScreen() {
  const theme = useTheme();
  const analyze = useAnalyzeStock();
  const sessions = useCommitteeSessions();
  const [symbol, setSymbol] = useState("");
  const [result, setResult] = useState<CommitteeResult | null>(null);

  const verdictColor = (action: string) =>
    action === "BUY"
      ? theme.colors.positive
      : action === "SKIP"
        ? theme.colors.negative
        : theme.colors.textMuted;

  const stanceColor = (stance: string) =>
    stance === "bullish"
      ? theme.colors.positive
      : stance === "bearish" || stance === "caution"
        ? theme.colors.negative
        : theme.colors.textMuted;

  const run = () => {
    const sym = symbol.trim().toUpperCase();
    if (!sym) return;
    analyze.mutate(sym, {
      onSuccess: (r) => setResult(r),
      onError: (err: any) =>
        Alert.alert(
          "Couldn't analyze",
          err?.response?.data?.detail ?? "Check the symbol and try again.",
        ),
    });
  };

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.colors.background }} edges={["top"]}>
      <ScrollView contentContainerStyle={{ padding: 16, gap: 14 }}>
        <Text style={[styles.h1, { color: theme.colors.text }]}>AI Committee</Text>
        <Text style={[styles.sub, { color: theme.colors.textMuted }]}>
          A panel of AI analysts debate a stock over its real price data, and a CIO
          gives a verdict: Buy, Skip, or Hold. Educational — not advice.
        </Text>

        <View style={styles.addRow}>
          <TextInput
            value={symbol}
            onChangeText={setSymbol}
            onSubmitEditing={run}
            autoCapitalize="characters"
            autoCorrect={false}
            placeholder="Ticker — e.g. AAPL, VFV.TO"
            placeholderTextColor={theme.colors.textMuted}
            style={[
              styles.input,
              {
                borderColor: theme.colors.border,
                backgroundColor: theme.colors.surfaceAlt,
                color: theme.colors.text,
                borderRadius: theme.radius.md,
              },
            ]}
          />
          <Button title="Analyze" onPress={run} loading={analyze.isPending} style={styles.btn} />
        </View>

        {analyze.isPending ? (
          <Card>
            <View style={styles.pending}>
              <ActivityIndicator color={theme.colors.primary} />
              <Text style={{ color: theme.colors.textMuted, flex: 1 }}>
                The committee is deliberating — this can take up to a couple of
                minutes on local AI.
              </Text>
            </View>
          </Card>
        ) : null}

        {result ? (
          <>
            <Card>
              <View style={styles.verdictRow}>
                <View style={{ flex: 1 }}>
                  <Text style={{ color: theme.colors.textMuted, fontSize: 13 }}>
                    Verdict on {result.symbol}
                  </Text>
                  <Text style={[styles.verdict, { color: verdictColor(result.action) }]}>
                    {result.action}
                  </Text>
                  <Text style={{ color: theme.colors.textMuted, fontSize: 12 }}>
                    {Math.round(result.confidence * 100)}% confidence ·{" "}
                    {result.source === "ai" ? "AI committee" : "rules (AI offline)"}
                  </Text>
                </View>
              </View>
              <Text style={{ color: theme.colors.text, fontSize: 14, lineHeight: 21, marginTop: 8 }}>
                {result.summary}
              </Text>
            </Card>

            <Card>
              <Text style={[styles.cardTitle, { color: theme.colors.text }]}>The panel</Text>
              {result.agents.map((a: AgentTake) => (
                <View key={a.agent} style={styles.agentRow}>
                  <View style={[styles.dot, { backgroundColor: stanceColor(a.stance) }]} />
                  <View style={{ flex: 1 }}>
                    <Text style={{ color: theme.colors.text, fontWeight: "600", fontSize: 14 }}>
                      {a.name}{" "}
                      <Text style={{ color: stanceColor(a.stance), fontWeight: "700" }}>
                        {a.stance}
                      </Text>
                    </Text>
                    <Text style={{ color: theme.colors.textMuted, fontSize: 13, marginTop: 2 }}>
                      {a.thesis}
                    </Text>
                  </View>
                </View>
              ))}
            </Card>

            <Text style={[styles.disclaimer, { color: theme.colors.textMuted }]}>
              {result.disclaimer}
            </Text>
          </>
        ) : null}

        {(sessions.data ?? []).length > 0 ? (
          <Card>
            <Text style={[styles.cardTitle, { color: theme.colors.text }]}>Recent analyses</Text>
            {(sessions.data ?? []).slice(0, 8).map((s) => (
              <View key={s.id} style={styles.sessionRow}>
                <Text style={{ color: theme.colors.text, fontWeight: "600" }}>{s.symbol}</Text>
                <Text style={{ color: verdictColor(s.action), fontWeight: "700" }}>
                  {s.action}
                </Text>
                <Text style={{ color: theme.colors.textMuted, fontSize: 12 }}>
                  {new Date(s.created_at).toLocaleDateString()}
                </Text>
              </View>
            ))}
          </Card>
        ) : null}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  h1: { fontSize: 28, fontWeight: "800" },
  sub: { fontSize: 13, lineHeight: 19 },
  addRow: { flexDirection: "row", gap: 10 },
  input: {
    flex: 1,
    borderWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 12,
    height: 44,
    fontSize: 14,
  },
  btn: { width: 100, height: 44 },
  pending: { flexDirection: "row", alignItems: "center", gap: 12 },
  cardTitle: { fontSize: 16, fontWeight: "700", marginBottom: 4 },
  verdictRow: { flexDirection: "row", alignItems: "center" },
  verdict: { fontSize: 30, fontWeight: "800", marginTop: 2 },
  agentRow: { flexDirection: "row", gap: 10, marginTop: 12, alignItems: "flex-start" },
  dot: { width: 10, height: 10, borderRadius: 5, marginTop: 5 },
  sessionRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingVertical: 8,
  },
  disclaimer: { fontSize: 11, lineHeight: 15, textAlign: "center" },
});
