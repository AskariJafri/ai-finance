import { useNavigation } from "@react-navigation/native";
import { StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { useTheme } from "../theme";

/**
 * Web build of the Link Bank screen.
 *
 * `react-native-plaid-link-sdk` is native-only and has no browser build, so on
 * web we can't import it at all (it would crash the whole bundle at load).
 * Metro picks this `.web` file automatically when running in a browser. Bank
 * linking stays a phone-only flow; the rest of the app works here, and the
 * built-in mock bank still populates data on the backend.
 */
export function LinkBankScreen() {
  const theme = useTheme();
  const navigation = useNavigation<any>();

  return (
    <SafeAreaView
      style={[styles.container, { backgroundColor: theme.colors.background }]}
    >
      <Card style={{ gap: 12 }}>
        <Text style={[styles.title, { color: theme.colors.text }]}>
          Bank linking is phone-only
        </Text>
        <Text style={{ color: theme.colors.textMuted, lineHeight: 20 }}>
          Connecting a real bank uses Plaid's native SDK, which only runs in the
          mobile app. Everything else works here in the browser — and the demo
          backend's mock bank already seeds accounts and transactions so you can
          explore Dashboard, Insights, Invest and Ask without linking.
        </Text>
        <Button title="Go back" onPress={() => navigation.goBack()} />
      </Card>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, alignItems: "center", justifyContent: "center", padding: 20 },
  title: { fontSize: 20, fontWeight: "800" },
});
