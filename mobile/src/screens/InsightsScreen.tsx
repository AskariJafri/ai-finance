import { useMemo, useState } from "react";
import {
  ActivityIndicator,
  Modal,
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
  useBudgetRule,
  useBudgetSettings,
  useMonthlyReport,
  useSetBudgetSettings,
} from "../api/hooks";
import { AdviceCard } from "../components/AdviceCard";
import { BudgetRuleBars } from "../components/BudgetRuleBars";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { MonthlyBarsChart } from "../components/MonthlyBarsChart";
import { MonthSwitcher } from "../components/MonthSwitcher";
import { useTheme } from "../theme";
import { formatCurrency, formatPercent } from "../utils/format";

export function InsightsScreen() {
  const theme = useTheme();
  const report = useMonthlyReport(6);
  const [selectedMonth, setSelectedMonth] = useState<string | null>(null);
  const [editingIncome, setEditingIncome] = useState(false);

  const months = report.data?.months ?? [];
  const monthKeys = useMemo(() => months.map((m) => m.month), [months]);
  // Default to the latest month with data.
  const month = selectedMonth ?? monthKeys[monthKeys.length - 1];
  const current = months.find((m) => m.month === month);

  const rule = useBudgetRule(month);

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.colors.background }} edges={["top"]}>
      <Text style={[styles.h1, { color: theme.colors.text }]}>Insights</Text>

      {report.isLoading ? (
        <View style={styles.center}>
          <ActivityIndicator color={theme.colors.primary} />
        </View>
      ) : months.length === 0 || !month ? (
        <View style={styles.center}>
          <Text style={{ color: theme.colors.textMuted, textAlign: "center" }}>
            No data yet. Connect a bank and sync some transactions to see
            monthly reports here.
          </Text>
        </View>
      ) : (
        <ScrollView
          contentContainerStyle={{ padding: 16, gap: 14 }}
          refreshControl={
            <RefreshControl
              refreshing={report.isRefetching}
              onRefresh={() => {
                report.refetch();
                rule.refetch();
              }}
              tintColor={theme.colors.primary}
            />
          }
        >
          <MonthSwitcher months={monthKeys} selected={month} onSelect={setSelectedMonth} />

          <Card>
            <Text style={[styles.cardTitle, { color: theme.colors.text }]}>
              Last {months.length} months
            </Text>
            <View style={{ marginTop: 12 }}>
              <MonthlyBarsChart
                months={months}
                selected={month}
                onSelect={setSelectedMonth}
              />
            </View>
          </Card>

          {current ? (
            <Card>
              <Text style={[styles.cardTitle, { color: theme.colors.text }]}>
                Summary
              </Text>
              <View style={styles.statsRow}>
                <Stat
                  label="Income"
                  value={formatCurrency(current.income)}
                  color={theme.colors.positive}
                />
                <Stat
                  label="Spending"
                  value={formatCurrency(current.spending)}
                  color={theme.colors.negative}
                />
                <Stat
                  label="Net"
                  value={formatCurrency(current.net)}
                  color={theme.colors.text}
                />
              </View>
              {current.by_category.length > 0 ? (
                <View style={{ marginTop: 14, gap: 8 }}>
                  {current.by_category.map((c) => (
                    <View key={c.category} style={styles.legendRow}>
                      <View
                        style={[
                          styles.legendDot,
                          { backgroundColor: c.color ?? theme.colors.primary },
                        ]}
                      />
                      <Text
                        numberOfLines={1}
                        style={[styles.legendLabel, { color: theme.colors.text }]}
                      >
                        {c.category}
                      </Text>
                      <Text style={[styles.legendValue, { color: theme.colors.textMuted }]}>
                        {formatCurrency(c.amount)}
                      </Text>
                    </View>
                  ))}
                </View>
              ) : null}
            </Card>
          ) : null}

          <Card>
            <View style={styles.cardHeaderRow}>
              <Text style={[styles.cardTitle, { color: theme.colors.text }]}>
                50/30/20 rule
              </Text>
              {rule.data ? (
                <Pressable onPress={() => setEditingIncome(true)} hitSlop={8}>
                  <Text style={{ color: theme.colors.primary, fontSize: 13 }}>
                    income {rule.data.income_source === "detected" ? "auto-detected" : "manual"} · edit
                  </Text>
                </Pressable>
              ) : null}
            </View>
            {rule.data ? (
              <>
                <Text style={{ color: theme.colors.textMuted, fontSize: 13, marginBottom: 12 }}>
                  {formatCurrency(rule.data.income)} income — 50% needs, 30% wants,
                  20% savings
                </Text>
                <BudgetRuleBars report={rule.data} />
              </>
            ) : rule.isLoading ? (
              <ActivityIndicator color={theme.colors.primary} style={{ marginTop: 12 }} />
            ) : null}
          </Card>

          {month ? (
            <Card>
              <Text style={[styles.cardTitle, { color: theme.colors.text, marginBottom: 10 }]}>
                AI coach
              </Text>
              <AdviceCard month={month} />
            </Card>
          ) : null}
        </ScrollView>
      )}

      <IncomeOverrideModal visible={editingIncome} onClose={() => setEditingIncome(false)} />
    </SafeAreaView>
  );
}

function IncomeOverrideModal({
  visible,
  onClose,
}: {
  visible: boolean;
  onClose: () => void;
}) {
  const theme = useTheme();
  const settings = useBudgetSettings();
  const save = useSetBudgetSettings();
  const [value, setValue] = useState<string | null>(null);

  const currentOverride = settings.data?.monthly_income_override;
  const text = value ?? (currentOverride != null ? String(currentOverride) : "");

  const submit = (override: number | null) => {
    save.mutate(
      { monthly_income_override: override },
      {
        onSuccess: () => {
          setValue(null);
          onClose();
        },
      },
    );
  };

  const parsed = Number(text.replace(/[^0-9.]/g, ""));

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose}>
        <Pressable style={{ width: "100%" }} onPress={() => {}}>
          <Card style={{ margin: 12 }}>
            <Text style={[styles.cardTitle, { color: theme.colors.text }]}>
              Monthly income for 50/30/20
            </Text>
            <Text style={{ color: theme.colors.textMuted, fontSize: 13, marginTop: 4 }}>
              By default income is detected from your deposits each month. Set a
              fixed amount here to override it.
            </Text>
            <TextInput
              value={text}
              onChangeText={setValue}
              keyboardType="decimal-pad"
              placeholder="e.g. 4500"
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
            <View style={{ gap: 10, marginTop: 14 }}>
              <Button
                title="Save"
                loading={save.isPending}
                disabled={!text || !(parsed > 0)}
                onPress={() => submit(parsed)}
              />
              {currentOverride != null ? (
                <Button
                  title="Clear override (auto-detect)"
                  variant="secondary"
                  onPress={() => submit(null)}
                />
              ) : null}
            </View>
          </Card>
        </Pressable>
      </Pressable>
    </Modal>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color: string }) {
  const theme = useTheme();
  return (
    <View style={{ flex: 1 }}>
      <Text style={{ fontSize: 13, color: theme.colors.textMuted }}>{label}</Text>
      <Text style={[styles.statValue, { color }]}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  h1: { fontSize: 28, fontWeight: "800", paddingHorizontal: 16, paddingVertical: 12 },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24 },
  cardTitle: { fontSize: 16, fontWeight: "700" },
  cardHeaderRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: 4,
  },
  statsRow: { flexDirection: "row", marginTop: 12, gap: 12 },
  statValue: { fontSize: 17, fontWeight: "700", marginTop: 2 },
  legendRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  legendDot: { width: 10, height: 10, borderRadius: 5 },
  legendLabel: { flex: 1, fontSize: 14 },
  legendValue: { fontSize: 14, fontWeight: "600" },
  backdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.45)",
    justifyContent: "flex-end",
  },
  input: {
    marginTop: 12,
    borderWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 14,
    height: 48,
    fontSize: 16,
  },
});
