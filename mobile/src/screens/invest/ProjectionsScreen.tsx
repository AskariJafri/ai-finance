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

import { useExplainers, useProjection } from "../../api/hooks";
import { Button } from "../../components/Button";
import { Card } from "../../components/Card";
import { ExplainerSheet } from "../../components/MetricTile";
import { FanChart } from "../../components/charts/FanChart";
import { useTheme } from "../../theme";
import { formatPercent } from "../../utils/format";

const HORIZONS = [1, 5, 10] as const;
const METHODS = ["gbm", "bootstrap"] as const;

export function ProjectionsScreen() {
  const theme = useTheme();
  const [horizon, setHorizon] = useState<(typeof HORIZONS)[number]>(5);
  const [method, setMethod] = useState<(typeof METHODS)[number]>("gbm");
  const [contribution, setContribution] = useState("0");
  const [explainerKey, setExplainerKey] = useState<string | null>(null);
  const projection = useProjection();
  const explainers = useExplainers().data ?? {};

  const run = () =>
    projection.mutate({
      horizon_years: horizon,
      method,
      monthly_contribution: Number(contribution.replace(/[^0-9.]/g, "")) || 0,
    });

  const result = projection.data;

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.colors.background }} edges={["top"]}>
      <ScrollView contentContainerStyle={{ padding: 16, gap: 14 }}>
        <Text style={[styles.h1, { color: theme.colors.text }]}>Projections</Text>

        <Card>
          <Text style={[styles.label, { color: theme.colors.textMuted }]}>Horizon</Text>
          <View style={styles.segmentRow}>
            {HORIZONS.map((h) => (
              <Segment key={h} active={horizon === h} label={`${h}y`} onPress={() => setHorizon(h)} />
            ))}
          </View>
          <View style={styles.methodRow}>
            <Text style={[styles.label, { color: theme.colors.textMuted }]}>Method</Text>
            <Pressable onPress={() => setExplainerKey(method)} hitSlop={8}>
              <Text style={{ color: theme.colors.primary, fontSize: 12 }}>What's this? ⓘ</Text>
            </Pressable>
          </View>
          <View style={styles.segmentRow}>
            {METHODS.map((m) => (
              <Segment
                key={m}
                active={method === m}
                label={m === "gbm" ? "GBM (bell curve)" : "Bootstrap (real days)"}
                onPress={() => setMethod(m)}
              />
            ))}
          </View>
          <Text style={[styles.label, { color: theme.colors.textMuted, marginTop: 12 }]}>
            Monthly contribution (USD)
          </Text>
          <TextInput
            value={contribution}
            onChangeText={setContribution}
            keyboardType="decimal-pad"
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
          <Button
            title="Run 2,000 simulations"
            onPress={run}
            loading={projection.isPending}
            style={{ marginTop: 14 }}
          />
        </Card>

        {result ? (
          <>
            <Card>
              <Text style={[styles.cardTitle, { color: theme.colors.text, marginBottom: 12 }]}>
                {horizon}-year outlook ({result.assumptions.n_paths} paths)
              </Text>
              <FanChart
                months={result.months}
                bands={result.bands}
                startValue={result.start_value}
              />
              <Pressable onPress={() => setExplainerKey("fan_chart")} hitSlop={8}>
                <Text style={{ color: theme.colors.primary, fontSize: 12, marginTop: 8 }}>
                  How do I read this? ⓘ
                </Text>
              </Pressable>
            </Card>

            <Card>
              <Text style={[styles.cardTitle, { color: theme.colors.text }]}>
                Chance of ending below what you put in
              </Text>
              <Text style={[styles.probValue, { color: theme.colors.text }]}>
                {formatPercent(result.prob_loss)}
              </Text>
              <Text style={{ color: theme.colors.textMuted, fontSize: 13, lineHeight: 19 }}>
                {formatPercent(result.prob_loss)} of the {result.assumptions.n_paths}{" "}
                simulated futures ended worth less than your starting value plus
                contributions. Assumptions from your portfolio's last{" "}
                {result.assumptions.window_days} trading days: average return{" "}
                {formatPercent(result.assumptions.mu_ann)}/yr, volatility{" "}
                {formatPercent(result.assumptions.sigma_ann)}/yr.
              </Text>
              <Text style={[styles.framing, { color: theme.colors.textMuted }]}>
                {result.framing}
              </Text>
            </Card>
            <Text style={[styles.meta, { color: theme.colors.textMuted }]}>
              {result.disclaimer}
            </Text>
          </>
        ) : !projection.isPending ? (
          <Text style={{ color: theme.colors.textMuted, textAlign: "center", padding: 12 }}>
            Configure and run — you'll get a range of futures, never a single
            "prediction". That's how institutional planners do it.
          </Text>
        ) : null}
      </ScrollView>

      {explainerKey && explainers[explainerKey] ? (
        <ExplainerSheet
          explainer={explainers[explainerKey]!}
          visible={!!explainerKey}
          onClose={() => setExplainerKey(null)}
        />
      ) : null}
    </SafeAreaView>
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
  segmentRow: { flexDirection: "row", gap: 8, marginTop: 8 },
  segment: {
    flex: 1,
    height: 38,
    borderRadius: 8,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 4,
  },
  methodRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginTop: 14,
  },
  input: {
    marginTop: 8,
    borderWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 14,
    height: 44,
    fontSize: 15,
  },
  probValue: { fontSize: 30, fontWeight: "800", marginVertical: 6 },
  framing: { fontSize: 12, fontStyle: "italic", marginTop: 10 },
  meta: { fontSize: 11, lineHeight: 15 },
});
