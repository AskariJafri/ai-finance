import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  ViewStyle,
} from "react-native";

import { useTheme } from "../theme";

export function Button({
  title,
  onPress,
  loading,
  disabled,
  variant = "primary",
  style,
}: {
  title: string;
  onPress: () => void;
  loading?: boolean;
  disabled?: boolean;
  variant?: "primary" | "secondary";
  style?: ViewStyle;
}) {
  const theme = useTheme();
  const isPrimary = variant === "primary";
  const bg = isPrimary ? theme.colors.primary : theme.colors.surfaceAlt;
  const fg = isPrimary ? theme.colors.primaryText : theme.colors.text;
  const isDisabled = disabled || loading;

  return (
    <Pressable
      onPress={onPress}
      disabled={isDisabled}
      style={({ pressed }) => [
        styles.button,
        {
          backgroundColor: bg,
          borderRadius: theme.radius.md,
          opacity: isDisabled ? 0.55 : pressed ? 0.85 : 1,
        },
        style,
      ]}
    >
      {loading ? (
        <ActivityIndicator color={fg} />
      ) : (
        <Text style={[styles.label, { color: fg }]}>{title}</Text>
      )}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  button: {
    height: 52,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 20,
  },
  label: { fontSize: 16, fontWeight: "600" },
});
