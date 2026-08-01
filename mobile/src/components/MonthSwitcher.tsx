import { Pressable, StyleSheet, Text, View } from "react-native";

import { useTheme } from "../theme";
import { formatMonth } from "../utils/format";

/** `‹ July 2026 ›` bounded by the months that actually have data. */
export function MonthSwitcher({
  months,
  selected,
  onSelect,
}: {
  months: string[]; // ascending "YYYY-MM"
  selected: string;
  onSelect: (month: string) => void;
}) {
  const theme = useTheme();
  const idx = months.indexOf(selected);
  const prev = idx > 0 ? months[idx - 1] : undefined;
  const next = idx >= 0 ? months[idx + 1] : undefined;

  return (
    <View style={styles.row}>
      <Arrow
        label="‹"
        enabled={!!prev}
        onPress={() => prev && onSelect(prev)}
      />
      <Text style={[styles.title, { color: theme.colors.text }]}>
        {formatMonth(selected)}
      </Text>
      <Arrow
        label="›"
        enabled={!!next}
        onPress={() => next && onSelect(next)}
      />
    </View>
  );
}

function Arrow({
  label,
  enabled,
  onPress,
}: {
  label: string;
  enabled: boolean;
  onPress: () => void;
}) {
  const theme = useTheme();
  return (
    <Pressable onPress={onPress} hitSlop={12} disabled={!enabled}>
      <Text
        style={[
          styles.arrow,
          { color: enabled ? theme.colors.primary : theme.colors.border },
        ]}
      >
        {label}
      </Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 8,
  },
  title: { fontSize: 18, fontWeight: "700" },
  arrow: { fontSize: 26, fontWeight: "600", paddingHorizontal: 8 },
});
