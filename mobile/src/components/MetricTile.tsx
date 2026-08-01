import { useState } from "react";
import { Modal, Pressable, StyleSheet, Text, View } from "react-native";

import { Explainer } from "../api/types";
import { Card } from "./Card";
import { useTheme } from "../theme";

/** A metric with hero value, context sentence, and a "What is this?" sheet. */
export function MetricTile({
  label,
  value,
  context,
  explainer,
}: {
  label: string;
  value: string;
  context?: string;
  explainer?: Explainer;
}) {
  const theme = useTheme();
  const [open, setOpen] = useState(false);

  return (
    <View style={[styles.tile, { backgroundColor: theme.colors.surfaceAlt, borderRadius: theme.radius.md }]}>
      <View style={styles.labelRow}>
        <Text style={[styles.label, { color: theme.colors.textMuted }]}>{label}</Text>
        {explainer ? (
          <Pressable onPress={() => setOpen(true)} hitSlop={10}>
            <Text style={{ color: theme.colors.primary, fontSize: 12 }}>ⓘ</Text>
          </Pressable>
        ) : null}
      </View>
      <Text style={[styles.value, { color: theme.colors.text }]}>{value}</Text>
      {context ? (
        <Text style={[styles.context, { color: theme.colors.textMuted }]}>{context}</Text>
      ) : null}

      {explainer ? (
        <ExplainerSheet explainer={explainer} visible={open} onClose={() => setOpen(false)} />
      ) : null}
    </View>
  );
}

export function ExplainerSheet({
  explainer,
  visible,
  onClose,
}: {
  explainer: Explainer;
  visible: boolean;
  onClose: () => void;
}) {
  const theme = useTheme();
  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose}>
        <Pressable style={{ width: "100%" }} onPress={() => {}}>
          <Card style={{ margin: 12 }}>
            <Text style={[styles.sheetTitle, { color: theme.colors.text }]}>
              {explainer.title}
            </Text>
            <Text style={[styles.sheetShort, { color: theme.colors.primary }]}>
              {explainer.short}
            </Text>
            <Text style={[styles.sheetBody, { color: theme.colors.text }]}>
              {explainer.body}
            </Text>
            <Text style={[styles.sheetCaveat, { color: theme.colors.textMuted }]}>
              ⚠︎ {explainer.caveat}
            </Text>
          </Card>
        </Pressable>
      </Pressable>
    </Modal>
  );
}

const styles = StyleSheet.create({
  tile: { padding: 12, flexGrow: 1, flexBasis: "45%" },
  labelRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  label: { fontSize: 12 },
  value: { fontSize: 20, fontWeight: "800", marginTop: 2 },
  context: { fontSize: 11, marginTop: 4, lineHeight: 15 },
  backdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.45)",
    justifyContent: "flex-end",
  },
  sheetTitle: { fontSize: 18, fontWeight: "800" },
  sheetShort: { fontSize: 14, fontWeight: "600", marginTop: 6 },
  sheetBody: { fontSize: 14, lineHeight: 21, marginTop: 10 },
  sheetCaveat: { fontSize: 12, lineHeight: 17, marginTop: 10 },
});
