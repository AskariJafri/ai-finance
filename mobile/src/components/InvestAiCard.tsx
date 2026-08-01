import { ActivityIndicator, StyleSheet, Text, View } from "react-native";

import { useGenerateInvestInsight, useInvestInsight } from "../api/hooks";
import { Button } from "./Button";
import { useTheme } from "../theme";

export function InvestAiCard() {
  const theme = useTheme();
  const insight = useInvestInsight();
  const generate = useGenerateInvestInsight();

  const current = insight.data;
  const hasContent = !!current?.content;

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
            <Text style={[styles.note, { color: theme.colors.textMuted }]}>
              AI offline — showing rule-based checks.
            </Text>
          ) : null}
          <Text style={{ color: theme.colors.text, fontSize: 14, lineHeight: 21 }}>
            {current!.content}
          </Text>
        </>
      ) : (
        <Text style={{ color: theme.colors.textMuted }}>
          A monthly health check of your portfolio: risk level, concentration,
          drift from targets — computed by rules, explained in plain language.
        </Text>
      )}

      {!generate.isPending ? (
        <Button
          title={hasContent ? "Regenerate health check" : "Run health check"}
          variant={hasContent ? "secondary" : "primary"}
          onPress={() => generate.mutate({ force: hasContent })}
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
  note: { fontSize: 12, fontStyle: "italic" },
  disclaimer: { fontSize: 11, lineHeight: 15 },
});
