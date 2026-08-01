import { Component, ReactNode } from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { Button } from "./Button";
import { Card } from "./Card";
import { useTheme } from "../theme";

/**
 * Catches render/runtime errors from its subtree so a crash in one area
 * (e.g. the Invest stack) degrades to a recoverable screen instead of taking
 * the whole app down. In a release build an uncaught JS error closes the app
 * with no red-box; this boundary turns that into a visible, retryable message
 * — and prints the error text so the failure can actually be diagnosed.
 */
type Props = { children: ReactNode; label?: string };
type State = { error: Error | null };

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error) {
    // Surface it in the JS console / dev logs as well.
    console.error("ErrorBoundary caught:", error);
  }

  reset = () => this.setState({ error: null });

  render() {
    if (this.state.error) {
      return <Fallback error={this.state.error} label={this.props.label} onReset={this.reset} />;
    }
    return this.props.children;
  }
}

function Fallback({
  error,
  label,
  onReset,
}: {
  error: Error;
  label?: string;
  onReset: () => void;
}) {
  const theme = useTheme();
  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.colors.background }} edges={["top"]}>
      <ScrollView contentContainerStyle={styles.container}>
        <Card>
          <Text style={[styles.title, { color: theme.colors.text }]}>
            {label ? `${label} hit a snag` : "Something went wrong"}
          </Text>
          <Text style={[styles.body, { color: theme.colors.textMuted }]}>
            This screen ran into an error and was stopped so the rest of the app
            keeps working. Tap Try again — if it keeps happening, the message
            below helps pinpoint the cause.
          </Text>
          <View style={[styles.errBox, { backgroundColor: theme.colors.surfaceAlt }]}>
            <Text style={[styles.errText, { color: theme.colors.danger }]} selectable>
              {error.message || String(error)}
            </Text>
          </View>
          <Button title="Try again" onPress={onReset} style={{ marginTop: 14 }} />
        </Card>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { padding: 16, flexGrow: 1, justifyContent: "center" },
  title: { fontSize: 18, fontWeight: "800" },
  body: { fontSize: 14, lineHeight: 21, marginTop: 8 },
  errBox: { marginTop: 14, padding: 12, borderRadius: 10 },
  errText: { fontSize: 12, fontFamily: "monospace" },
});
