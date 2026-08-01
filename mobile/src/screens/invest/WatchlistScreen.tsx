import { useNavigation } from "@react-navigation/native";
import { useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { useAddWatch, useRemoveWatch, useWatchlist } from "../../api/hooks";
import { Button } from "../../components/Button";
import { Card } from "../../components/Card";
import { useTheme } from "../../theme";
import { formatCurrency, formatPercent } from "../../utils/format";

export function WatchlistScreen() {
  const theme = useTheme();
  const navigation = useNavigation<any>();
  const { data, isLoading, refetch, isRefetching } = useWatchlist();
  const add = useAddWatch();
  const remove = useRemoveWatch();
  const [symbol, setSymbol] = useState("");

  const items = data ?? [];

  const onAdd = () => {
    const sym = symbol.trim().toUpperCase();
    if (!sym) return;
    add.mutate(sym, {
      onSuccess: () => setSymbol(""),
      onError: (err: any) =>
        Alert.alert(
          "Couldn't add",
          err?.response?.data?.detail ??
            "Check the symbol. Canadian listings need '.TO' (e.g. VFV.TO).",
        ),
    });
  };

  const confirmRemove = (id: string, sym: string) =>
    Alert.alert("Remove", `Remove ${sym} from your watchlist?`, [
      { text: "Cancel", style: "cancel" },
      { text: "Remove", style: "destructive", onPress: () => remove.mutate(id) },
    ]);

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.colors.background }} edges={["top"]}>
      <Text style={[styles.h1, { color: theme.colors.text }]}>Watchlist</Text>

      <View style={styles.addRow}>
        <TextInput
          value={symbol}
          onChangeText={setSymbol}
          onSubmitEditing={onAdd}
          autoCapitalize="characters"
          autoCorrect={false}
          returnKeyType="done"
          placeholder="Add a ticker — e.g. AAPL, VFV.TO"
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
        <Button title="Add" onPress={onAdd} loading={add.isPending} style={styles.addBtn} />
      </View>

      {isLoading ? (
        <View style={styles.center}>
          <ActivityIndicator color={theme.colors.primary} />
        </View>
      ) : (
        <ScrollView
          contentContainerStyle={{ padding: 16, paddingTop: 4, gap: 10 }}
          refreshControl={
            <RefreshControl
              refreshing={isRefetching}
              onRefresh={refetch}
              tintColor={theme.colors.primary}
            />
          }
        >
          {items.length === 0 ? (
            <Text style={{ color: theme.colors.textMuted, textAlign: "center", marginTop: 40 }}>
              Nothing tracked yet. Add a ticker above to watch its live price.
            </Text>
          ) : (
            items.map((it) => {
              const up = (it.change_pct ?? 0) >= 0;
              const changeColor = up ? theme.colors.positive : theme.colors.negative;
              return (
                <Card key={it.id}>
                  <View style={styles.row}>
                    <Pressable
                      style={({ pressed }) => [styles.rowMain, { opacity: pressed ? 0.6 : 1 }]}
                      onPress={() =>
                        navigation.navigate("SymbolChart", { symbol: it.symbol })
                      }
                    >
                    <View style={{ flex: 1 }}>
                      <Text style={[styles.symbol, { color: theme.colors.text }]}>
                        {it.symbol}
                        <Text style={{ color: theme.colors.textMuted, fontWeight: "400", fontSize: 12 }}>
                          {"  "}
                          {it.currency}
                        </Text>
                      </Text>
                      {it.name ? (
                        <Text
                          numberOfLines={1}
                          style={{ color: theme.colors.textMuted, fontSize: 12, marginTop: 2 }}
                        >
                          {it.name}
                        </Text>
                      ) : null}
                    </View>

                    <View style={{ alignItems: "flex-end" }}>
                      {it.stale || it.price == null ? (
                        <Text style={{ color: theme.colors.textMuted, fontSize: 13 }}>
                          No price
                        </Text>
                      ) : (
                        <>
                          <Text style={{ color: theme.colors.text, fontWeight: "700", fontSize: 15 }}>
                            {formatCurrency(it.price, it.currency)}
                          </Text>
                          {it.change_pct != null ? (
                            <Text style={{ color: changeColor, fontSize: 12 }}>
                              {up ? "▲" : "▼"} {formatPercent(Math.abs(it.change_pct))}
                            </Text>
                          ) : null}
                        </>
                      )}
                    </View>
                    </Pressable>

                    <Pressable
                      onPress={() => confirmRemove(it.id, it.symbol)}
                      hitSlop={10}
                      style={{ marginLeft: 14 }}
                    >
                      <Text style={{ color: theme.colors.danger, fontSize: 16 }}>✕</Text>
                    </Pressable>
                  </View>
                </Card>
              );
            })
          )}

          <Text style={[styles.note, { color: theme.colors.textMuted }]}>
            Prices are delayed (~15 min) and shown in each listing's native
            currency. Day change is vs the previous session close.
          </Text>
        </ScrollView>
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  h1: { fontSize: 28, fontWeight: "800", paddingHorizontal: 16, paddingVertical: 12 },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  addRow: { flexDirection: "row", gap: 10, paddingHorizontal: 16, paddingBottom: 10 },
  input: {
    flex: 1,
    borderWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 12,
    height: 44,
    fontSize: 14,
  },
  addBtn: { width: 84, height: 44 },
  row: { flexDirection: "row", alignItems: "center" },
  // Everything except the remove button opens the candle chart.
  rowMain: { flex: 1, flexDirection: "row", alignItems: "center" },
  symbol: { fontSize: 16, fontWeight: "700" },
  note: { fontSize: 11, lineHeight: 15, marginTop: 6, paddingBottom: 8 },
});
