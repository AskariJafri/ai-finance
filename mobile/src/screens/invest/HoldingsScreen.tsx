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

import {
  useDeleteHolding,
  useHoldings,
  useLookupSymbol,
  usePortfolio,
  useSetCash,
  useUpsertHolding,
} from "../../api/hooks";
import { Button } from "../../components/Button";
import { Card } from "../../components/Card";
import { useTheme } from "../../theme";
import { formatCurrency } from "../../utils/format";

export function HoldingsScreen() {
  const theme = useTheme();
  const holdings = useHoldings();
  const portfolio = usePortfolio();
  const lookup = useLookupSymbol();
  const upsert = useUpsertHolding();
  const remove = useDeleteHolding();
  const setCash = useSetCash();

  const [symbol, setSymbol] = useState("");
  const [qty, setQty] = useState("");
  const [cost, setCost] = useState("");
  const [cash, setCashText] = useState("");

  const check = () => {
    if (!symbol.trim()) return;
    lookup.mutate(symbol.trim().toUpperCase(), {
      onError: (err: any) =>
        Alert.alert(
          "Ticker not found",
          err?.response?.data?.detail ??
            "Check the symbol. Canadian listings need '.TO' (e.g. VFV.TO).",
        ),
    });
  };

  const add = () => {
    const q = Number(qty);
    const c = Number(cost);
    if (!symbol.trim() || !(q > 0)) {
      Alert.alert("Missing info", "Enter a ticker and the number of shares.");
      return;
    }
    upsert.mutate(
      { symbol: symbol.trim().toUpperCase(), qty: q, avg_cost: c > 0 ? c : 0 },
      {
        onSuccess: () => {
          setSymbol("");
          setQty("");
          setCost("");
          lookup.reset();
        },
        onError: (err: any) =>
          Alert.alert("Failed", err?.response?.data?.detail ?? "Could not add holding."),
      },
    );
  };

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.colors.background }} edges={["top"]}>
      <ScrollView contentContainerStyle={{ padding: 16, gap: 14 }}>
        <Text style={[styles.h1, { color: theme.colors.text }]}>Holdings</Text>

        <Card>
          <Text style={[styles.cardTitle, { color: theme.colors.text }]}>Add a holding</Text>
          <Text style={[styles.help, { color: theme.colors.textMuted }]}>
            Enter what you own at your broker (Wealthsimple, Questrade, TD…).
            Canadian listings use the “.TO” suffix — e.g. VFV.TO, XEQT.TO,
            ZAG.TO. US tickers work as-is (AAPL, SPY) and are converted to CAD
            automatically.
          </Text>

          <Field label="Ticker" value={symbol} onChange={setSymbol} onBlur={check} caps />
          {lookup.data ? (
            <Text style={{ color: theme.colors.positive, fontSize: 13, marginTop: 6 }}>
              ✓ {lookup.data.name ?? lookup.data.symbol} —{" "}
              {formatCurrency(lookup.data.price, lookup.data.currency)}{" "}
              {lookup.data.currency}
            </Text>
          ) : lookup.isPending ? (
            <ActivityIndicator color={theme.colors.primary} style={{ marginTop: 6 }} />
          ) : null}

          <Field label="Shares" value={qty} onChange={setQty} numeric />
          <Field
            label="Average cost per share (optional)"
            value={cost}
            onChange={setCost}
            numeric
          />
          <Button
            title="Add / update holding"
            onPress={add}
            loading={upsert.isPending}
            style={{ marginTop: 12 }}
          />
        </Card>

        <Card>
          <Text style={[styles.cardTitle, { color: theme.colors.text }]}>Cash</Text>
          <Text style={[styles.help, { color: theme.colors.textMuted }]}>
            Un-invested cash in your brokerage account (CAD). The rebalancer
            deploys this toward your targets.
          </Text>
          <View style={{ flexDirection: "row", gap: 10, alignItems: "flex-end" }}>
            <View style={{ flex: 1 }}>
              <Field
                label=""
                value={cash}
                onChange={setCashText}
                numeric
                placeholder={String(portfolio.data?.account?.cash ?? 0)}
              />
            </View>
            <Button
              title="Save"
              onPress={() => setCash.mutate(Number(cash) || 0)}
              loading={setCash.isPending}
              style={{ width: 100, height: 42 }}
            />
          </View>
        </Card>

        <Card>
          <Text style={[styles.cardTitle, { color: theme.colors.text }]}>
            Your holdings
          </Text>
          {holdings.isLoading ? (
            <ActivityIndicator color={theme.colors.primary} style={{ marginTop: 12 }} />
          ) : (holdings.data ?? []).length === 0 ? (
            <Text style={{ color: theme.colors.textMuted, marginTop: 10 }}>
              Nothing added yet.
            </Text>
          ) : (
            (holdings.data ?? []).map((h) => {
              const pos = portfolio.data?.positions.find((p) => p.symbol === h.symbol);
              return (
                <View key={h.id} style={styles.row}>
                  <View style={{ flex: 1 }}>
                    <Text style={{ color: theme.colors.text, fontWeight: "700" }}>
                      {h.symbol}
                      <Text style={{ color: theme.colors.textMuted, fontWeight: "400" }}>
                        {"  "}
                        {h.currency}
                      </Text>
                    </Text>
                    <Text style={{ color: theme.colors.textMuted, fontSize: 12 }}>
                      {h.qty} shares @ {formatCurrency(h.avg_cost, h.currency)} cost
                      {h.is_synced ? "  ·  synced" : ""}
                    </Text>
                  </View>
                  <View style={{ alignItems: "flex-end" }}>
                    {pos ? (
                      <>
                        <Text style={{ color: theme.colors.text, fontWeight: "600" }}>
                          {formatCurrency(pos.market_value)}
                        </Text>
                        <Text
                          style={{
                            color:
                              pos.unrealized_pl >= 0
                                ? theme.colors.positive
                                : theme.colors.negative,
                            fontSize: 12,
                          }}
                        >
                          {pos.unrealized_pl >= 0 ? "+" : ""}
                          {formatCurrency(pos.unrealized_pl)}
                        </Text>
                      </>
                    ) : null}
                  </View>
                  <Pressable
                    onPress={() =>
                      h.is_synced
                        ? // Removing it locally would be undone by the next
                          // sync, so say so rather than pretending it worked.
                          Alert.alert(
                            "Synced holding",
                            `${h.symbol} comes from your linked brokerage account. Close the position at your broker, then sync — removing it here would come back on the next sync.`,
                          )
                        : Alert.alert("Remove holding", `Remove ${h.symbol}?`, [
                            { text: "Cancel", style: "cancel" },
                            {
                              text: "Remove",
                              style: "destructive",
                              onPress: () => remove.mutate(h.id),
                            },
                          ])
                    }
                    hitSlop={10}
                    style={{ marginLeft: 12 }}
                  >
                    <Text style={{ color: theme.colors.danger, fontSize: 16 }}>✕</Text>
                  </Pressable>
                </View>
              );
            })
          )}
        </Card>
      </ScrollView>
    </SafeAreaView>
  );
}

function Field({
  label,
  value,
  onChange,
  onBlur,
  numeric,
  caps,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  onBlur?: () => void;
  numeric?: boolean;
  caps?: boolean;
  placeholder?: string;
}) {
  const theme = useTheme();
  return (
    <View style={{ marginTop: label ? 10 : 0 }}>
      {label ? (
        <Text style={{ fontSize: 13, color: theme.colors.textMuted }}>{label}</Text>
      ) : null}
      <TextInput
        value={value}
        onChangeText={onChange}
        onBlur={onBlur}
        autoCapitalize={caps ? "characters" : "none"}
        keyboardType={numeric ? "decimal-pad" : "default"}
        placeholder={placeholder}
        placeholderTextColor={theme.colors.textMuted}
        style={{
          marginTop: 6,
          borderWidth: StyleSheet.hairlineWidth,
          borderColor: theme.colors.border,
          backgroundColor: theme.colors.surfaceAlt,
          borderRadius: theme.radius.md,
          color: theme.colors.text,
          paddingHorizontal: 12,
          height: 42,
          fontSize: 14,
        }}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  h1: { fontSize: 28, fontWeight: "800" },
  cardTitle: { fontSize: 16, fontWeight: "700" },
  help: { fontSize: 13, lineHeight: 19, marginTop: 6, marginBottom: 4 },
  row: {
    flexDirection: "row",
    alignItems: "center",
    marginTop: 12,
    gap: 10,
  },
});
