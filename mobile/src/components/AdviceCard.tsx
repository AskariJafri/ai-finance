import { ActivityIndicator, StyleSheet, Text, View } from "react-native";

import { useAdvice, useGenerateAdvice } from "../api/hooks";
import { Button } from "./Button";
import { useTheme } from "../theme";

export function AdviceCard({ month }: { month: string }) {
  const theme = useTheme();
  const advice = useAdvice(month);
  const generate = useGenerateAdvice();

  const current = advice.data;
  const hasContent = !!current?.content;

  const onGenerate = (force: boolean) => generate.mutate({ month, force });

  return (
    <View style={{ gap: 12 }}>
      {generate.isPending ? (
        <View style={styles.pending}>
          <ActivityIndicator color={theme.colors.primary} />
          <Text style={{ color: theme.colors.textMuted, flex: 1 }}>
            Running local AI — this can take a minute…
          </Text>
        </View>
      ) : hasContent ? (
        <>
          {current!.source === "fallback" ? (
            <Text style={[styles.fallbackNote, { color: theme.colors.textMuted }]}>
              AI offline — showing basic tips.
            </Text>
          ) : null}
          <Text style={{ color: theme.colors.text, fontSize: 14, lineHeight: 21 }}>
            {current!.content}
          </Text>
        </>
      ) : (
        <Text style={{ color: theme.colors.textMuted }}>
          Get a plain-language read on this month's numbers — how you track
          against 50/30/20 and what to do with what's left over.
        </Text>
      )}

      {!generate.isPending ? (
        <Button
          title={hasContent ? "Regenerate advice" : "Generate advice"}
          variant={hasContent ? "secondary" : "primary"}
          onPress={() => onGenerate(hasContent)}
        />
      ) : null}

      {current?.disclaimer ? (
        <Text style={[styles.disclaimer, { color: theme.colors.textMuted }]}>
          {current.disclaimer}
        </Text>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  pending: { flexDirection: "row", alignItems: "center", gap: 12, paddingVertical: 8 },
  fallbackNote: { fontSize: 12, fontStyle: "italic" },
  disclaimer: { fontSize: 11, lineHeight: 15 },
});
