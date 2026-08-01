import { useState } from "react";
import { Modal, Pressable, StyleSheet, Text, TextInput, View } from "react-native";

import { ProposedOrder } from "../api/types";
import { Button } from "./Button";
import { Card } from "./Card";
import { useTheme } from "../theme";
import { formatCurrency } from "../utils/format";

/**
 * A proposed trade. Two ways to settle it:
 *
 * * **Manual** — the user places it at their own broker and taps "I placed
 *   this", which records the fill. Always available.
 * * **Live** — with a trade-enabled brokerage link, "Place at broker" submits
 *   the real order. Gated behind its own confirmation because it spends real
 *   money, and never happens without this explicit tap.
 */
export function ProposedOrderCard({
  order,
  onExecuted,
  onReject,
  onApprove,
  onRefresh,
  canTrade = false,
  brokerName,
  busy,
}: {
  order: ProposedOrder;
  onExecuted: (id: string, fillPrice?: number) => void;
  onReject: (id: string) => void;
  onApprove?: (id: string) => void;
  onRefresh?: (id: string) => void;
  canTrade?: boolean;
  brokerName?: string | null;
  busy: boolean;
}) {
  const theme = useTheme();
  const [confirming, setConfirming] = useState(false);
  const [confirmingLive, setConfirmingLive] = useState(false);
  const [price, setPrice] = useState("");
  const isBuy = order.side === "buy";
  const open = order.status === "proposed";
  const live = canTrade && onApprove != null && order.qty != null;
  const pending = order.status === "submitted";

  return (
    <View
      style={[
        styles.card,
        { backgroundColor: theme.colors.surfaceAlt, borderRadius: theme.radius.md },
      ]}
    >
      <View style={styles.headerRow}>
        <Text
          style={[
            styles.side,
            { color: isBuy ? theme.colors.positive : theme.colors.negative },
          ]}
        >
          {order.side.toUpperCase()}
        </Text>
        <Text style={[styles.symbol, { color: theme.colors.text }]}>{order.symbol}</Text>
        <Text style={[styles.value, { color: theme.colors.text }]}>
          {formatCurrency(order.est_value)}
        </Text>
      </View>
      {order.qty != null ? (
        <Text style={[styles.qty, { color: theme.colors.text }]}>
          {order.qty} share{order.qty === 1 ? "" : "s"}
        </Text>
      ) : null}
      <Text style={[styles.reason, { color: theme.colors.textMuted }]}>{order.reason}</Text>

      {open ? (
        <>
          <View style={styles.actions}>
            <Button
              title={live ? "Place at broker" : "I placed this"}
              onPress={() => (live ? setConfirmingLive(true) : setConfirming(true))}
              disabled={busy}
              style={styles.actionBtn}
            />
            <Button
              title="Skip"
              variant="secondary"
              onPress={() => onReject(order.id)}
              disabled={busy}
              style={styles.actionBtn}
            />
          </View>
          {live ? (
            // Escape hatch: the user may still trade outside the app.
            <Pressable onPress={() => setConfirming(true)} hitSlop={6}>
              <Text style={[styles.link, { color: theme.colors.textMuted }]}>
                I placed it myself
              </Text>
            </Pressable>
          ) : null}
        </>
      ) : (
        <View style={{ gap: 6 }}>
          <Text style={[styles.status, { color: theme.colors.textMuted }]}>
            {order.status === "filled" ? "✓ done" : order.status}
            {order.status_detail ? ` — ${order.status_detail}` : ""}
          </Text>
          {pending && onRefresh ? (
            <Button
              title="Check status"
              variant="secondary"
              onPress={() => onRefresh(order.id)}
              disabled={busy}
              style={{ height: 38 }}
            />
          ) : null}
        </View>
      )}

      <Modal
        visible={confirmingLive}
        transparent
        animationType="slide"
        onRequestClose={() => setConfirmingLive(false)}
      >
        <Pressable style={styles.backdrop} onPress={() => setConfirmingLive(false)}>
          <Pressable style={{ width: "100%" }} onPress={() => {}}>
            <Card style={{ margin: 12 }}>
              <Text style={[styles.modalTitle, { color: theme.colors.text }]}>
                Place this order for real?
              </Text>
              {/* Restate the order in full: this is the last screen before
                  real money moves, so nothing is left implicit. */}
              <Text
                style={{
                  color: theme.colors.text,
                  fontSize: 16,
                  fontWeight: "700",
                  marginTop: 10,
                }}
              >
                {order.side.toUpperCase()} {order.qty} {order.symbol}
                {"  ·  ≈"}
                {formatCurrency(order.est_value)}
              </Text>
              <Text style={{ color: theme.colors.textMuted, fontSize: 13, marginTop: 8 }}>
                This sends a real order to{" "}
                {brokerName ?? "your linked brokerage account"} and spends real
                money. The final price may differ from the estimate above.
              </Text>
              <Button
                title="Confirm — place the order"
                loading={busy}
                onPress={() => {
                  onApprove?.(order.id);
                  setConfirmingLive(false);
                }}
                style={{ marginTop: 14 }}
              />
              <Button
                title="Cancel"
                variant="secondary"
                onPress={() => setConfirmingLive(false)}
                style={{ marginTop: 8 }}
              />
            </Card>
          </Pressable>
        </Pressable>
      </Modal>

      <Modal
        visible={confirming}
        transparent
        animationType="slide"
        onRequestClose={() => setConfirming(false)}
      >
        <Pressable style={styles.backdrop} onPress={() => setConfirming(false)}>
          <Pressable style={{ width: "100%" }} onPress={() => {}}>
            <Card style={{ margin: 12 }}>
              <Text style={[styles.modalTitle, { color: theme.colors.text }]}>
                Placed at your broker?
              </Text>
              <Text style={{ color: theme.colors.textMuted, fontSize: 13, marginTop: 6 }}>
                Confirm you {isBuy ? "bought" : "sold"} {order.qty} share
                {order.qty === 1 ? "" : "s"} of {order.symbol}. Your holdings
                will update to match.
              </Text>
              <Text style={{ color: theme.colors.textMuted, fontSize: 13, marginTop: 12 }}>
                Fill price per share (optional — uses latest close if blank)
              </Text>
              <TextInput
                value={price}
                onChangeText={setPrice}
                keyboardType="decimal-pad"
                placeholder="e.g. 188.67"
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
                title="Confirm — update my holdings"
                loading={busy}
                onPress={() => {
                  const p = Number(price);
                  onExecuted(order.id, p > 0 ? p : undefined);
                  setConfirming(false);
                  setPrice("");
                }}
                style={{ marginTop: 14 }}
              />
            </Card>
          </Pressable>
        </Pressable>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  card: { padding: 12, gap: 6 },
  headerRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  side: { fontSize: 13, fontWeight: "800" },
  symbol: { fontSize: 16, fontWeight: "700", flex: 1 },
  value: { fontSize: 15, fontWeight: "700" },
  qty: { fontSize: 14, fontWeight: "600" },
  reason: { fontSize: 13, lineHeight: 18 },
  status: { fontSize: 12, fontStyle: "italic" },
  link: { fontSize: 12, textDecorationLine: "underline", marginTop: 2 },
  actions: { flexDirection: "row", gap: 10, marginTop: 4 },
  actionBtn: { flex: 1, height: 42 },
  backdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.45)",
    justifyContent: "flex-end",
  },
  modalTitle: { fontSize: 17, fontWeight: "700" },
  input: {
    marginTop: 8,
    borderWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 14,
    height: 46,
    fontSize: 16,
  },
});
