import { Pressable, StyleSheet, Text, View } from "react-native";

import { Transaction } from "../api/types";
import { useTheme } from "../theme";
import { formatDate, formatSigned } from "../utils/format";

export function TransactionRow({
  txn,
  onPress,
}: {
  txn: Transaction;
  onPress?: () => void;
}) {
  const theme = useTheme();
  const isInflow = txn.amount < 0;
  const chipColor = txn.category?.color ?? theme.colors.textMuted;

  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [
        styles.row,
        { opacity: pressed ? 0.6 : 1, borderBottomColor: theme.colors.border },
      ]}
    >
      <View style={[styles.dot, { backgroundColor: chipColor }]} />
      <View style={styles.middle}>
        <Text numberOfLines={1} style={[styles.title, { color: theme.colors.text }]}>
          {txn.merchant_name ?? txn.name}
        </Text>
        <Text style={[styles.sub, { color: theme.colors.textMuted }]}>
          {(txn.category?.name ?? "Uncategorized") + " · " + formatDate(txn.date)}
          {txn.category_source === "ai" ? "  · AI" : ""}
          {txn.pending ? "  · pending" : ""}
        </Text>
      </View>
      {txn.is_transfer ? (
        <View style={[styles.transferChip, { backgroundColor: theme.colors.surfaceAlt }]}>
          <Text style={{ color: theme.colors.textMuted, fontSize: 11, fontWeight: "600" }}>
            Transfer
          </Text>
        </View>
      ) : null}
      <Text
        style={[
          styles.amount,
          { color: isInflow ? theme.colors.positive : theme.colors.text },
        ]}
      >
        {formatSigned(txn.amount, txn.currency)}
      </Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 12,
    borderBottomWidth: StyleSheet.hairlineWidth,
    gap: 12,
  },
  dot: { width: 10, height: 10, borderRadius: 5 },
  middle: { flex: 1 },
  title: { fontSize: 15, fontWeight: "600" },
  sub: { fontSize: 12, marginTop: 2 },
  amount: { fontSize: 15, fontWeight: "700" },
  transferChip: {
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 10,
    marginRight: 4,
  },
});
