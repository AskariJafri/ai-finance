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

import { useExplainers, useScenarios } from "../../api/hooks";
import { Card } from "../../components/Card";
import { ExplainerSheet } from "../../components/MetricTile";
import { useTheme } from "../../theme";
import { formatCurrency, formatPercent } from "../../utils/format";

export function ScenariosScreen() {
  const theme = useTheme();
  const { data, isLoading, error } = useScenarios();
  const [expanded, setExpanded] = useState<string | null>(null);
  const [showExplainer, setShowExplainer] = useState(false);
  const explainers = useExplainers().data ?? {};

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.colors.background }} edges={["top"]}>
      <View style={styles.headerRow}>
        <Text style={[styles.h1, { color: theme.colors.text }]}>Stress Test</Text>
        <Pressable onPress={() => setShowExplainer(true)} hitSlop={8}>
          <Text style={{ color: theme.colors.primary, fontSize: 13 }}>What is this? ⓘ</Text>
        </Pressable>
      </View>

      {isLoading ? (
        <View style={styles.center}>
          <ActivityIndicator color={theme.colors.primary} />
        </View>
      ) : error || !data ? (
        <View style={styles.center}>
          <Text style={{ color: theme.colors.textMuted, textAlign: "center", padding: 24 }}>
            Connect Alpaca, hold some positions, and run a sync to stress-test
            your portfolio.
          </Text>
        </View>
      ) : (
        <ScrollView contentContainerStyle={{ padding: 16, gap: 12 }}>
          {data.map((s) => {
            const open = expanded === s.key;
            return (
              <Pressable key={s.key} onPress={() => setExpanded(open ? null : s.key)}>
                <Card>
                  <View style={styles.row}>
                    <View style={{ flex: 1 }}>
                      <Text style={[styles.name, { color: theme.colors.text }]}>{s.name}</Text>
                      {s.index_recovery.months ? (
                        <Text style={{ color: theme.colors.textMuted, fontSize: 12, marginTop: 2 }}>
                          {s.index_recovery.index} recovered in ~{s.index_recovery.months} months
                        </Text>
                      ) : null}
                    </View>
                    <View style={{ alignItems: "flex-end" }}>
                      <Text
                        style={{
                          color:
                            s.portfolio_impact.amount < 0
                              ? theme.colors.negative
                              : theme.colors.positive,
                          fontWeight: "800",
                          fontSize: 16,
                        }}
                      >
                        {s.portfolio_impact.amount >= 0 ? "+" : ""}
                        {formatCurrency(s.portfolio_impact.amount)}
                      </Text>
                      <Text style={{ color: theme.colors.textMuted, fontSize: 12 }}>
                        {formatPercent(s.portfolio_impact.pct)}
                      </Text>
                    </View>
                  </View>

                  {open ? (
                    <View style={{ marginTop: 12, gap: 8 }}>
                      <Text style={{ color: theme.colors.text, fontSize: 13, lineHeight: 19 }}>
                        {s.description}
                      </Text>
                      {s.per_position.map((p) => (
                        <View key={p.symbol} style={styles.positionRow}>
                          <Text style={{ color: theme.colors.text, fontSize: 13 }}>{p.symbol}</Text>
                          <Text
                            style={{
                              color: p.impact < 0 ? theme.colors.negative : theme.colors.positive,
                              fontSize: 13,
                            }}
                          >
                            {p.impact >= 0 ? "+" : ""}
                            {formatCurrency(p.impact)} ({formatPercent(p.shock_pct)})
                          </Text>
                        </View>
                      ))}
                      <Text style={{ color: theme.colors.textMuted, fontSize: 11 }}>
                        {s.index_recovery.note}
                      </Text>
                      <Text style={{ color: theme.colors.textMuted, fontSize: 11 }}>
                        Source: {s.source}. First-order estimate.
                      </Text>
                    </View>
                  ) : null}
                </Card>
              </Pressable>
            );
          })}
          {data[0] ? (
            <Text style={[styles.meta, { color: theme.colors.textMuted }]}>
              {data[0].disclaimer}
            </Text>
          ) : null}
        </ScrollView>
      )}

      {explainers.stress_test ? (
        <ExplainerSheet
          explainer={explainers.stress_test}
          visible={showExplainer}
          onClose={() => setShowExplainer(false)}
        />
      ) : null}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  headerRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 16,
    paddingVertical: 12,
  },
  h1: { fontSize: 28, fontWeight: "800" },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  row: { flexDirection: "row", alignItems: "center", gap: 10 },
  name: { fontSize: 15, fontWeight: "700" },
  positionRow: { flexDirection: "row", justifyContent: "space-between" },
  meta: { fontSize: 11, lineHeight: 15, textAlign: "center", paddingBottom: 8 },
});
