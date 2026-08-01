import { useMemo, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Modal,
  Pressable,
  ScrollView,
  SectionList,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import {
  useCategories,
  useCreateCategory,
  useTransactions,
  useUpdateCategory,
} from "../api/hooks";
import { Card } from "../components/Card";
import { TransactionRow } from "../components/TransactionRow";
import { Transaction } from "../api/types";
import { useTheme } from "../theme";
import { formatCurrency, formatMonth } from "../utils/format";
import { isIncome, isSpending } from "../utils/totals";
import { CUSTOM_CATEGORY_PALETTE } from "../utils/categoryPalette";

type MonthSection = {
  key: string; // "YYYY-MM"
  title: string;
  spent: number;
  income: number;
  data: Transaction[];
};

function groupByMonth(txns: Transaction[]): MonthSection[] {
  const byKey = new Map<string, MonthSection>();
  for (const t of txns) {
    const key = t.date.slice(0, 7);
    let section = byKey.get(key);
    if (!section) {
      section = { key, title: formatMonth(key), spent: 0, income: 0, data: [] };
      byKey.set(key, section);
    }
    if (isSpending(t)) section.spent += t.amount;
    else if (isIncome(t)) section.income += -t.amount;
    section.data.push(t);
  }
  // API returns transactions newest-first, so insertion order is already
  // newest month first and rows within a month stay date-descending.
  return [...byKey.values()];
}

type CategoryTally = {
  cat: NonNullable<Transaction["category"]>;
  spent: number;
  count: number;
};

export function TransactionsScreen() {
  const theme = useTheme();
  const { data, isLoading, refetch, isRefetching } = useTransactions();
  const categories = useCategories();
  const updateCategory = useUpdateCategory();
  const createCategory = useCreateCategory();
  const [editing, setEditing] = useState<Transaction | null>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [filterCategoryId, setFilterCategoryId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [newBucket, setNewBucket] = useState<"needs" | "wants">("wants");
  const [newColor, setNewColor] = useState(CUSTOM_CATEGORY_PALETTE[0]);

  const all = useMemo(() => data ?? [], [data]);

  // Categories actually present in the loaded transactions, each with its total
  // spend and count — so the filter bar only offers real options, ordered by
  // how much was spent (most relevant first).
  const present = useMemo(() => {
    const map = new Map<string, CategoryTally>();
    for (const t of all) {
      if (!t.category) continue;
      const e = map.get(t.category.id) ?? { cat: t.category, spent: 0, count: 0 };
      if (isSpending(t)) e.spent += t.amount;
      e.count += 1;
      map.set(t.category.id, e);
    }
    return [...map.values()].sort((a, b) => b.spent - a.spent);
  }, [all]);

  // A filter can point at a category that's no longer present after a refresh;
  // fall back to showing everything rather than an empty screen.
  const activeCat = useMemo(
    () => present.find((p) => p.cat.id === filterCategoryId) ?? null,
    [present, filterCategoryId],
  );

  const filtered = useMemo(
    () => (activeCat ? all.filter((t) => t.category?.id === activeCat.cat.id) : all),
    [all, activeCat],
  );

  const sections = useMemo(() => groupByMonth(filtered), [filtered]);

  const toggle = (key: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const onPick = (categoryId: string) => {
    if (!editing) return;
    updateCategory.mutate(
      { transactionId: editing.id, categoryId },
      { onSettled: () => setEditing(null) },
    );
  };

  const onCreateCategory = () => {
    const name = newName.trim();
    if (!name) return;
    createCategory.mutate(
      { name, group: "expense", color: newColor, budget_bucket: newBucket },
      {
        onSuccess: () => {
          setNewName("");
          setNewBucket("wants");
          setNewColor(CUSTOM_CATEGORY_PALETTE[0]);
          setCreating(false);
        },
      },
    );
  };

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.colors.background }} edges={["top"]}>
      <Text style={[styles.h1, { color: theme.colors.text }]}>Transactions</Text>

      {!isLoading && present.length > 0 ? (
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.chipRow}
        >
          <Chip label="All" active={!activeCat} onPress={() => setFilterCategoryId(null)} />
          {present.map((p) => (
            <Chip
              key={p.cat.id}
              label={p.cat.name}
              color={p.cat.color ?? undefined}
              active={activeCat?.cat.id === p.cat.id}
              onPress={() => setFilterCategoryId(p.cat.id)}
            />
          ))}
        </ScrollView>
      ) : null}

      {activeCat ? (
        <View style={[styles.summary, { borderBottomColor: theme.colors.border }]}>
          <Text style={{ color: theme.colors.textMuted, fontSize: 13 }}>
            <Text style={{ color: theme.colors.text, fontWeight: "700" }}>
              {activeCat.cat.name}
            </Text>
            {"  ·  "}
            <Text style={{ color: theme.colors.text, fontWeight: "700" }}>
              {formatCurrency(activeCat.spent)}
            </Text>{" "}
            spent · {activeCat.count} transaction{activeCat.count === 1 ? "" : "s"}
          </Text>
        </View>
      ) : null}

      {isLoading ? (
        <View style={styles.center}>
          <ActivityIndicator color={theme.colors.primary} />
        </View>
      ) : (
        <SectionList
          sections={sections.map((s) =>
            collapsed.has(s.key) ? { ...s, data: [] } : s,
          )}
          keyExtractor={(t) => t.id}
          contentContainerStyle={{ padding: 16 }}
          refreshing={isRefetching}
          onRefresh={refetch}
          stickySectionHeadersEnabled
          ListEmptyComponent={
            <Text style={{ color: theme.colors.textMuted, textAlign: "center", marginTop: 40 }}>
              No transactions yet. Connect a bank from the Dashboard.
            </Text>
          }
          renderSectionHeader={({ section }) => (
            <Pressable
              onPress={() => toggle(section.key)}
              style={[
                styles.sectionHeader,
                {
                  backgroundColor: theme.colors.background,
                  borderBottomColor: theme.colors.border,
                },
              ]}
            >
              <Text style={[styles.sectionTitle, { color: theme.colors.text }]}>
                {section.title}
              </Text>
              <View style={styles.sectionTotals}>
                <Text style={[styles.sectionTotal, { color: theme.colors.negative }]}>
                  −{formatCurrency(section.spent)}
                </Text>
                <Text style={[styles.sectionTotal, { color: theme.colors.positive }]}>
                  +{formatCurrency(section.income)}
                </Text>
                <Text style={{ color: theme.colors.textMuted, fontSize: 12 }}>
                  {collapsed.has(section.key) ? "▸" : "▾"}
                </Text>
              </View>
            </Pressable>
          )}
          renderItem={({ item }) => (
            <TransactionRow txn={item} onPress={() => setEditing(item)} />
          )}
        />
      )}

      <Modal
        visible={!!editing}
        transparent
        animationType="slide"
        onRequestClose={() => setEditing(null)}
      >
        <Pressable style={styles.backdrop} onPress={() => setEditing(null)}>
          <Pressable style={{ width: "100%" }} onPress={() => {}}>
            <Card style={{ margin: 12 }}>
              <Text style={[styles.modalTitle, { color: theme.colors.text }]}>
                Categorize “{editing?.merchant_name ?? editing?.name}”
              </Text>
              <Text style={{ color: theme.colors.textMuted, marginBottom: 8, fontSize: 13 }}>
                Your choice trains future auto-categorization.
              </Text>
              {/* Above the list, not in a ListFooterComponent: the list is
                  capped at 360px and there are ~17 categories, so anything
                  after them is below the fold and effectively undiscoverable. */}
              <Pressable
                onPress={() => setCreating(true)}
                style={({ pressed }) => [
                  styles.newCategoryRow,
                  {
                    borderColor: theme.colors.primary,
                    opacity: pressed ? 0.6 : 1,
                  },
                ]}
              >
                <Text style={{ color: theme.colors.primary, fontWeight: "700", fontSize: 15 }}>
                  + New category
                </Text>
              </Pressable>
              <FlatList
                data={categories.data ?? []}
                keyExtractor={(c) => c.id}
                style={{ maxHeight: 360 }}
                renderItem={({ item }) => (
                  <Pressable
                    onPress={() => onPick(item.id)}
                    style={({ pressed }) => [
                      styles.catRow,
                      { borderBottomColor: theme.colors.border, opacity: pressed ? 0.6 : 1 },
                    ]}
                  >
                    <View
                      style={[styles.catDot, { backgroundColor: item.color ?? theme.colors.primary }]}
                    />
                    <Text style={[styles.catName, { color: theme.colors.text }]}>{item.name}</Text>
                    {editing?.category?.id === item.id ? (
                      <Text style={{ color: theme.colors.primary }}>✓</Text>
                    ) : null}
                  </Pressable>
                )}
              />
            </Card>
          </Pressable>
        </Pressable>
      </Modal>

      <Modal
        visible={creating}
        transparent
        animationType="slide"
        onRequestClose={() => setCreating(false)}
      >
        <Pressable style={styles.backdrop} onPress={() => setCreating(false)}>
          <Pressable style={{ width: "100%" }} onPress={() => {}}>
            <Card style={{ margin: 12 }}>
              <Text style={[styles.modalTitle, { color: theme.colors.text }]}>New category</Text>
              <TextInput
                value={newName}
                onChangeText={setNewName}
                placeholder="Name"
                placeholderTextColor={theme.colors.textMuted}
                style={[
                  styles.nameInput,
                  { color: theme.colors.text, borderColor: theme.colors.border },
                ]}
              />
              <View style={styles.bucketRow}>
                <Chip
                  label="Essential (needs)"
                  active={newBucket === "needs"}
                  onPress={() => setNewBucket("needs")}
                />
                <Chip
                  label="Lifestyle (wants)"
                  active={newBucket === "wants"}
                  onPress={() => setNewBucket("wants")}
                />
              </View>
              <View style={styles.swatchRow}>
                {CUSTOM_CATEGORY_PALETTE.map((c) => (
                  <Pressable
                    key={c}
                    onPress={() => setNewColor(c)}
                    style={[
                      styles.swatch,
                      { backgroundColor: c },
                      newColor === c ? { borderColor: theme.colors.text, borderWidth: 2 } : null,
                    ]}
                  />
                ))}
              </View>
              <Pressable
                onPress={onCreateCategory}
                disabled={!newName.trim() || createCategory.isPending}
                style={[
                  styles.createButton,
                  {
                    backgroundColor: theme.colors.primary,
                    opacity: !newName.trim() || createCategory.isPending ? 0.5 : 1,
                  },
                ]}
              >
                <Text style={{ color: theme.colors.primaryText, fontWeight: "700" }}>Create</Text>
              </Pressable>
            </Card>
          </Pressable>
        </Pressable>
      </Modal>
    </SafeAreaView>
  );
}

function Chip({
  label,
  color,
  active,
  onPress,
}: {
  label: string;
  color?: string;
  active: boolean;
  onPress: () => void;
}) {
  const theme = useTheme();
  return (
    <Pressable
      onPress={onPress}
      style={[
        styles.chip,
        {
          backgroundColor: active ? theme.colors.primary : theme.colors.surfaceAlt,
          borderColor: active ? theme.colors.primary : theme.colors.border,
        },
      ]}
    >
      {color ? <View style={[styles.chipDot, { backgroundColor: color }]} /> : null}
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
  h1: { fontSize: 28, fontWeight: "800", paddingHorizontal: 16, paddingVertical: 12 },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  chipRow: { paddingHorizontal: 16, paddingBottom: 10, gap: 8 },
  chip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingHorizontal: 12,
    height: 34,
    borderRadius: 17,
    borderWidth: StyleSheet.hairlineWidth,
  },
  chipDot: { width: 9, height: 9, borderRadius: 5 },
  summary: {
    paddingHorizontal: 16,
    paddingBottom: 10,
    borderBottomWidth: StyleSheet.hairlineWidth,
    marginBottom: 4,
  },
  sectionHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingVertical: 10,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  sectionTitle: { fontSize: 16, fontWeight: "700" },
  sectionTotals: { flexDirection: "row", alignItems: "center", gap: 10 },
  sectionTotal: { fontSize: 13, fontWeight: "600" },
  backdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.45)",
    justifyContent: "flex-end",
  },
  modalTitle: { fontSize: 17, fontWeight: "700", marginBottom: 4 },
  catRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingVertical: 14,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  catDot: { width: 12, height: 12, borderRadius: 6 },
  catName: { flex: 1, fontSize: 16 },
  newCategoryRow: {
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 10,
    paddingVertical: 12,
    alignItems: "center",
    marginBottom: 10,
  },
  nameInput: {
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 16,
    marginTop: 8,
    marginBottom: 12,
  },
  bucketRow: { flexDirection: "row", gap: 8, marginBottom: 12 },
  swatchRow: { flexDirection: "row", flexWrap: "wrap", gap: 10, marginBottom: 16 },
  swatch: { width: 28, height: 28, borderRadius: 14 },
  createButton: {
    height: 44,
    borderRadius: 10,
    alignItems: "center",
    justifyContent: "center",
  },
});
