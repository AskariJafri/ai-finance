import { useNavigation } from "@react-navigation/native";
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { useDashboard, useSync } from "../api/hooks";
import { CategorySpend, Transaction } from "../api/types";
import { BudgetRemainingCard } from "../components/BudgetRemainingCard";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { SpendDonut } from "../components/SpendDonut";
import { TransactionRow } from "../components/TransactionRow";
import { useSession } from "../stores/session";
import { useTheme } from "../theme";
import { formatCurrency, formatPercent } from "../utils/format";

export function DashboardScreen() {
  const theme = useTheme();
  const navigation = useNavigation<any>();
  const signOut = useSession((s) => s.signOut);
  const { data, isLoading, isError, refetch, isRefetching } = useDashboard();
  const sync = useSync();

  const hasData = data && data.recent_transactions.length > 0;

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.colors.background }} edges={["top"]}>
      <View style={styles.header}>
        <Text style={[styles.h1, { color: theme.colors.text }]}>Dashboard</Text>
        <View style={styles.headerActions}>
          <Pressable onPress={() => sync.mutate()} hitSlop={8}>
            <Text style={[styles.action, { color: theme.colors.primary }]}>
              {sync.isPending ? "Syncing…" : "Sync"}
            </Text>
          </Pressable>
          <Pressable onPress={() => signOut()} hitSlop={8}>
            <Text style={[styles.action, { color: theme.colors.textMuted }]}>Sign out</Text>
          </Pressable>
        </View>
      </View>

      {isLoading ? (
        <View style={styles.center}>
          <ActivityIndicator color={theme.colors.primary} />
        </View>
      ) : isError ? (
        <View style={styles.center}>
          <Text style={{ color: theme.colors.textMuted }}>
            Could not reach the server. Pull to retry.
          </Text>
        </View>
      ) : (
        <ScrollView
          contentContainerStyle={{ padding: 16, gap: 14 }}
          refreshControl={
            <RefreshControl refreshing={isRefetching} onRefresh={refetch} tintColor={theme.colors.primary} />
          }
        >
          <Card>
            <Text style={[styles.label, { color: theme.colors.textMuted }]}>Net worth</Text>
            <Text style={[styles.balance, { color: theme.colors.text }]}>
              {formatCurrency(data?.total_balance ?? 0)}
            </Text>
            <Text style={{ color: theme.colors.textMuted, fontSize: 13, marginTop: 2 }}>
              {formatCurrency(data?.cash_balance ?? 0)} cash − {formatCurrency(data?.credit_balance ?? 0)} owed
            </Text>
            <Text style={{ color: theme.colors.textMuted, fontSize: 12, marginTop: 12 }}>
              {data?.period_label ?? "Last 30 days"}
            </Text>
            <View style={styles.statsRow}>
              <Stat label="Income" value={formatCurrency(data?.period_income ?? 0)} color={theme.colors.positive} />
              <Stat label="Spending" value={formatCurrency(data?.period_spending ?? 0)} color={theme.colors.negative} />
              <Stat label="Savings" value={formatPercent(data?.savings_rate ?? 0)} color={theme.colors.text} />
            </View>
          </Card>

          <BudgetRemainingCard />

          {!hasData ? (
            <Card>
              <Text style={[styles.cardTitle, { color: theme.colors.text }]}>
                Connect a bank to get started
              </Text>
              <Text style={{ color: theme.colors.textMuted, marginVertical: 8 }}>
                Link an account with Plaid to import and categorize your transactions.
              </Text>
              <Button title="Connect a bank" onPress={() => navigation.navigate("LinkBank")} />
            </Card>
          ) : (
            <>
              <Card>
                <Text style={[styles.cardTitle, { color: theme.colors.text }]}>
                  This month's spending
                </Text>
                <View style={styles.donutRow}>
                  <SpendDonut data={data!.spend_by_category} size={140} strokeWidth={22} />
                  <View style={{ flex: 1, gap: 8 }}>
                    {data!.spend_by_category.slice(0, 3).map((c: CategorySpend) => (
                      <View key={c.category} style={styles.legendRow}>
                        <View
                          style={[styles.legendDot, { backgroundColor: c.color ?? theme.colors.primary }]}
                        />
                        <Text numberOfLines={1} style={[styles.legendLabel, { color: theme.colors.text }]}>
                          {c.category}
                        </Text>
                        <Text style={[styles.legendValue, { color: theme.colors.textMuted }]}>
                          {formatCurrency(c.amount)}
                        </Text>
                      </View>
                    ))}
                  </View>
                </View>
                <Pressable onPress={() => navigation.navigate("Insights")} hitSlop={8}>
                  <Text style={[styles.link, { color: theme.colors.primary }]}>
                    View full report →
                  </Text>
                </Pressable>
              </Card>

              <Card>
                <Text style={[styles.cardTitle, { color: theme.colors.text }]}>
                  Recent transactions
                </Text>
                {data!.recent_transactions.slice(0, 5).map((t: Transaction) => (
                  <TransactionRow
                    key={t.id}
                    txn={t}
                    onPress={() => navigation.navigate("Transactions")}
                  />
                ))}
                <Pressable onPress={() => navigation.navigate("Transactions")} hitSlop={8}>
                  <Text style={[styles.link, { color: theme.colors.primary }]}>
                    See all →
                  </Text>
                </Pressable>
              </Card>
            </>
          )}
        </ScrollView>
      )}
    </SafeAreaView>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color: string }) {
  const theme = useTheme();
  return (
    <View style={{ flex: 1 }}>
      <Text style={[styles.label, { color: theme.colors.textMuted }]}>{label}</Text>
      <Text style={[styles.statValue, { color }]}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  header: {
    paddingHorizontal: 16,
    paddingVertical: 12,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  headerActions: { flexDirection: "row", gap: 16 },
  action: { fontSize: 15, fontWeight: "600" },
  h1: { fontSize: 28, fontWeight: "800" },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24 },
  label: { fontSize: 13 },
  balance: { fontSize: 36, fontWeight: "800", marginTop: 4 },
  statsRow: { flexDirection: "row", marginTop: 16, gap: 12 },
  statValue: { fontSize: 18, fontWeight: "700", marginTop: 2 },
  cardTitle: { fontSize: 16, fontWeight: "700" },
  donutRow: { flexDirection: "row", alignItems: "center", gap: 16, marginTop: 12 },
  legendRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  legendDot: { width: 10, height: 10, borderRadius: 5 },
  legendLabel: { flex: 1, fontSize: 14 },
  legendValue: { fontSize: 14, fontWeight: "600" },
  link: { fontSize: 14, fontWeight: "600", marginTop: 12 },
});
