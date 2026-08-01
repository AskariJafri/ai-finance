import { useNavigation } from "@react-navigation/native";
import { useRef, useState } from "react";
import { Alert, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import {
  LinkLogLevel,
  create,
  destroy,
  open,
  usePlaidEmitter,
} from "react-native-plaid-link-sdk";

import { useCreateLinkToken, useExchangePublicToken } from "../api/hooks";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { useTheme } from "../theme";

export function LinkBankScreen() {
  const theme = useTheme();
  const navigation = useNavigation<any>();
  const linkToken = useCreateLinkToken();
  const exchange = useExchangePublicToken();
  const [busy, setBusy] = useState(false);
  // Last event carrying an error — OAuth banks often EXIT with a null error
  // while the real cause only appears on an earlier ERROR event.
  const lastErrorEvent = useRef<string | null>(null);

  usePlaidEmitter((event: any) => {
    console.log("[PlaidLink]", JSON.stringify(event));
    const m = event?.metadata;
    if (m?.errorCode || m?.errorMessage) {
      lastErrorEvent.current = `${event.eventName}: ${m.errorCode ?? ""} ${m.errorMessage ?? ""}`.trim();
    }
  });

  const onConnect = async () => {
    setBusy(true);
    lastErrorEvent.current = null;
    try {
      // A failed OAuth attempt can leave a dead native Link session behind;
      // clear it so this attempt starts fresh.
      await destroy().catch(() => {});
      const { link_token } = await linkToken.mutateAsync();
      create({ token: link_token, logLevel: LinkLogLevel.DEBUG });
      open({
        logLevel: LinkLogLevel.DEBUG,
        onSuccess: async (success: any) => {
          try {
            await exchange.mutateAsync({
              public_token: success.publicToken,
              institution_id: success.metadata?.institution?.id,
              institution_name: success.metadata?.institution?.name,
            });
            navigation.goBack();
          } catch {
            Alert.alert("Sync failed", "Linked, but we couldn't sync. Try Sync on the dashboard.");
          } finally {
            setBusy(false);
          }
        },
        onExit: (exit: any) => {
          setBusy(false);
          // Surface why Link exited — OAuth banks (e.g. TD/Chase) often exit
          // with a null error and only a metadata.status (e.g. requires_oauth),
          // which otherwise looks like "nothing happened".
          console.log("[PlaidLink] exit", JSON.stringify(exit));
          const err = exit?.error;
          const meta = exit?.metadata;
          const lines = [
            err?.displayMessage,
            err?.errorMessage,
            err?.errorCode ? `Code: ${JSON.stringify(err.errorCode)}` : null,
            lastErrorEvent.current ? `Last error event: ${lastErrorEvent.current}` : null,
            meta?.status ? `Status: ${meta.status}` : null,
            meta?.institution?.name ? `Institution: ${meta.institution.name}` : null,
            meta?.linkSessionId ? `Session: ${meta.linkSessionId}` : null,
            meta?.requestId ? `Request: ${meta.requestId}` : null,
          ].filter(Boolean);
          if (lines.length > 0) {
            Alert.alert("Plaid Link exited", lines.join("\n"));
          }
        },
      });
    } catch (err) {
      setBusy(false);
      Alert.alert(
        "Could not start Plaid Link",
        "Make sure you're running a dev build (not Expo Go) and the backend is reachable.",
      );
    }
  };

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.colors.background }}>
      <View style={styles.container}>
        <Text style={[styles.h1, { color: theme.colors.text }]}>Connect a bank</Text>
        <Card>
          <Text style={{ color: theme.colors.text, fontSize: 15, lineHeight: 22 }}>
            You'll be handed off to Plaid to sign in to your bank securely. We never see or store
            your bank credentials — only an encrypted access token.
          </Text>
          <Text style={{ color: theme.colors.textMuted, marginTop: 12, fontSize: 13 }}>
            Sandbox test login: username “user_good”, password “pass_good”.
          </Text>
        </Card>

        <Button
          title="Continue with Plaid"
          onPress={onConnect}
          loading={busy || linkToken.isPending || exchange.isPending}
          style={{ marginTop: 20 }}
        />
        <Button
          title="Cancel"
          variant="secondary"
          onPress={() => navigation.goBack()}
          style={{ marginTop: 12 }}
        />
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, padding: 20, gap: 4 },
  h1: { fontSize: 26, fontWeight: "800", marginBottom: 16 },
});
