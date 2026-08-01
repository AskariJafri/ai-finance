import { useNavigation } from "@react-navigation/native";
import * as Linking from "expo-linking";
import * as WebBrowser from "expo-web-browser";
import { useEffect, useState } from "react";
import {
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
  useBrokerageConnections,
  useCompleteBrokerageLink,
  useDeletePlaidCredentials,
  usePlaidStatus,
  useSetPlaidCredentials,
  useStartBrokerageLink,
  useSyncBrokerage,
  useUnlinkBrokerage,
} from "../api/hooks";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { useAppConfig } from "../stores/appConfig";
import { useSession } from "../stores/session";
import { useTheme } from "../theme";

export function SettingsScreen() {
  const theme = useTheme();
  const navigation = useNavigation<any>();
  const baseUrl = useAppConfig((s) => s.baseUrl);
  const setBaseUrl = useAppConfig((s) => s.setBaseUrl);
  const signOut = useSession((s) => s.signOut);

  const status = usePlaidStatus();
  const setCreds = useSetPlaidCredentials();
  const deleteCreds = useDeletePlaidCredentials();

  const connections = useBrokerageConnections();
  const startLink = useStartBrokerageLink();
  const completeLink = useCompleteBrokerageLink();
  const syncBrokerage = useSyncBrokerage();
  const unlinkBrokerage = useUnlinkBrokerage();

  const finishLinkBrokerage = async () => {
    try {
      const accounts = await completeLink.mutateAsync();
      Alert.alert(
        "Brokerage linked",
        `Linked ${accounts.length} account${accounts.length === 1 ? "" : "s"}. Sync to pull your positions in.`,
      );
    } catch (e: any) {
      Alert.alert("Not linked yet", detailOf(e) ?? "Please try again.");
    }
  };

  /**
   * Open SnapTrade's portal in an auth session and pull the authorized accounts
   * when it returns. Credentials are entered on SnapTrade's page, never here.
   *
   * The portal is told to redirect to the app's deep link on "Done", and
   * openAuthSessionAsync watches for exactly that redirect — so control comes
   * back automatically and we finish linking without the user hunting for a
   * button. If the session is dismissed some other way (a browser that ignores
   * the redirect), the "I've authorized" button below is the manual fallback.
   */
  const linkBrokerage = async () => {
    try {
      const redirectUri = Linking.createURL("brokerage-linked");
      const { portal_url } = await startLink.mutateAsync(redirectUri);
      const result = await WebBrowser.openAuthSessionAsync(portal_url, redirectUri);
      if (result.type === "success") {
        await finishLinkBrokerage();
      } else {
        // Dismissed without the redirect firing. The connection may still have
        // gone through on SnapTrade's side, so point at the manual finish.
        Alert.alert(
          "Almost there",
          'If you authorized your brokerage, tap "I\'ve authorized — finish linking" to pull your accounts in.',
        );
      }
    } catch (e: any) {
      Alert.alert("Couldn't start linking", detailOf(e) ?? "Please try again.");
    }
  };

  const confirmUnlink = () =>
    Alert.alert(
      "Unlink brokerage?",
      "Positions synced from your broker are removed. Holdings you entered by hand are kept.",
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Unlink",
          style: "destructive",
          onPress: () => unlinkBrokerage.mutate(),
        },
      ],
    );

  const [urlDraft, setUrlDraft] = useState(baseUrl);
  const [clientId, setClientId] = useState("");
  const [secret, setSecret] = useState("");
  const [env, setEnv] = useState<"sandbox" | "production">("sandbox");

  useEffect(() => {
    setUrlDraft(baseUrl);
  }, [baseUrl]);

  const onSaveUrl = async () => {
    await setBaseUrl(urlDraft);
    status.refetch();
    Alert.alert("Saved", "Server URL updated.");
  };

  const onSaveKeys = () => {
    if (!clientId.trim() || !secret.trim()) {
      Alert.alert("Missing fields", "Enter both client ID and secret.");
      return;
    }
    setCreds.mutate(
      { client_id: clientId.trim(), secret: secret.trim(), plaid_env: env },
      {
        onSuccess: () => {
          setSecret("");
          Alert.alert("Saved", "Your Plaid keys are stored securely.");
        },
        onError: () => Alert.alert("Failed", "Could not save keys. Check the server URL."),
      },
    );
  };

  const s = status.data;

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.colors.background }} edges={["top"]}>
      <ScrollView contentContainerStyle={{ padding: 16, gap: 14 }}>
        <Text style={[styles.h1, { color: theme.colors.text }]}>Settings</Text>

        {/* Server connection */}
        <Card>
          <Text style={[styles.cardTitle, { color: theme.colors.text }]}>Server connection</Text>
          <Text style={[styles.help, { color: theme.colors.textMuted }]}>
            The backend the app talks to. On a phone, use your computer's LAN IP
            (e.g. http://192.168.1.50:8000/api/v1).
          </Text>
          <TextInput
            value={urlDraft}
            onChangeText={setUrlDraft}
            autoCapitalize="none"
            keyboardType="url"
            placeholder="http://host:8000/api/v1"
            placeholderTextColor={theme.colors.textMuted}
            style={field(theme)}
          />
          <Button title="Save server URL" onPress={onSaveUrl} style={{ marginTop: 12 }} />
        </Card>

        {/* Plaid credentials */}
        <Card>
          <Text style={[styles.cardTitle, { color: theme.colors.text }]}>Plaid keys</Text>
          <View style={styles.statusRow}>
            <Text style={{ color: theme.colors.textMuted }}>Status</Text>
            <Text style={{ color: statusColor(theme, s?.source), fontWeight: "700" }}>
              {status.isLoading
                ? "…"
                : s?.source === "user"
                  ? `Your keys · ${s.plaid_env} · ${s.client_id_masked}`
                  : s?.source === "environment"
                    ? `Server keys · ${s.plaid_env}`
                    : "Mock (demo data)"}
            </Text>
          </View>
          <Text style={[styles.help, { color: theme.colors.textMuted }]}>
            Add your own Plaid API keys (from the Plaid dashboard). The secret is
            encrypted on the server; it is never shown again.
          </Text>

          <TextInput
            value={clientId}
            onChangeText={setClientId}
            autoCapitalize="none"
            placeholder="Plaid client_id"
            placeholderTextColor={theme.colors.textMuted}
            style={field(theme)}
          />
          <TextInput
            value={secret}
            onChangeText={setSecret}
            autoCapitalize="none"
            secureTextEntry
            placeholder="Plaid secret"
            placeholderTextColor={theme.colors.textMuted}
            style={field(theme)}
          />

          <View style={styles.envRow}>
            {(["sandbox", "production"] as const).map((e) => {
              const active = env === e;
              return (
                <Pressable
                  key={e}
                  onPress={() => setEnv(e)}
                  style={[
                    styles.envChip,
                    {
                      backgroundColor: active ? theme.colors.primary : theme.colors.surfaceAlt,
                      borderColor: theme.colors.border,
                    },
                  ]}
                >
                  <Text
                    style={{
                      color: active ? theme.colors.primaryText : theme.colors.text,
                      fontWeight: "600",
                    }}
                  >
                    {e}
                  </Text>
                </Pressable>
              );
            })}
          </View>

          <Button
            title="Save Plaid keys"
            onPress={onSaveKeys}
            loading={setCreds.isPending}
            style={{ marginTop: 12 }}
          />
          {s?.source === "user" && (
            <Button
              title="Remove my keys (use demo)"
              variant="secondary"
              onPress={() => deleteCreds.mutate()}
              loading={deleteCreds.isPending}
              style={{ marginTop: 10 }}
            />
          )}
        </Card>

        {/* Categories */}
        <Card>
          <Text style={[styles.cardTitle, { color: theme.colors.text }]}>Categories</Text>
          <Text style={[styles.help, { color: theme.colors.textMuted }]}>
            Rename, recolor or delete the categories you've created. Built-in
            categories can't be edited.
          </Text>
          <Button
            title="Manage categories"
            variant="secondary"
            onPress={() => navigation.navigate("ManageCategories")}
            style={{ marginTop: 12 }}
          />
        </Card>

        {/* Bank connections */}
        <Card>
          <Text style={[styles.cardTitle, { color: theme.colors.text }]}>Banks</Text>
          <Text style={[styles.help, { color: theme.colors.textMuted }]}>
            Link another bank account through Plaid.
          </Text>
          <Button
            title="Connect another bank"
            variant="secondary"
            onPress={() => navigation.navigate("LinkBank")}
            style={{ marginTop: 12 }}
          />
        </Card>

        {/* Brokerage connection (SnapTrade) */}
        <Card>
          <Text style={[styles.cardTitle, { color: theme.colors.text }]}>Brokerage</Text>
          {(connections.data ?? []).length === 0 ? (
            <>
              <Text style={[styles.help, { color: theme.colors.textMuted }]}>
                Link Wealthsimple or Questrade to pull your real positions in and
                place approved orders from the Rebalance tab. You authorize on
                SnapTrade's page — this app never sees your broker password.
              </Text>
              <Button
                title="Link my brokerage"
                onPress={linkBrokerage}
                loading={startLink.isPending}
                style={{ marginTop: 12 }}
              />
              <Button
                title="I've authorized — finish linking"
                variant="secondary"
                onPress={finishLinkBrokerage}
                loading={completeLink.isPending}
                style={{ marginTop: 8 }}
              />
            </>
          ) : (
            <>
              {(connections.data ?? []).map((c) => (
                <View key={c.id} style={{ marginTop: 10 }}>
                  <Text style={{ color: theme.colors.text, fontWeight: "700" }}>
                    {c.institution_name ?? "Brokerage"} · {c.account_name ?? "Account"}
                    {c.number_masked ? ` ${c.number_masked}` : ""}
                  </Text>
                  <Text style={{ color: theme.colors.textMuted, fontSize: 12, marginTop: 2 }}>
                    {c.trading_enabled
                      ? "Trading enabled — orders you approve are sent here."
                      : "Read-only — positions sync, but orders can't be placed."}
                  </Text>
                </View>
              ))}
              <Button
                title="Sync positions"
                onPress={() => syncBrokerage.mutate()}
                loading={syncBrokerage.isPending}
                style={{ marginTop: 12 }}
              />
              <Button
                title="Unlink brokerage"
                variant="secondary"
                onPress={confirmUnlink}
                loading={unlinkBrokerage.isPending}
                style={{ marginTop: 8 }}
              />
            </>
          )}
        </Card>

        <Button title="Sign out" variant="secondary" onPress={() => signOut()} />
      </ScrollView>
    </SafeAreaView>
  );
}

/** Pull the API's error message out of an axios failure, if there is one. */
function detailOf(e: any): string | undefined {
  return e?.response?.data?.detail;
}

function statusColor(theme: ReturnType<typeof useTheme>, source?: string) {
  if (source === "user") return theme.colors.positive;
  if (source === "environment") return theme.colors.primary;
  return theme.colors.textMuted;
}

function field(theme: ReturnType<typeof useTheme>) {
  return {
    height: 50,
    borderRadius: theme.radius.md,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: theme.colors.border,
    backgroundColor: theme.colors.background,
    color: theme.colors.text,
    paddingHorizontal: 14,
    fontSize: 15,
    marginTop: 10,
  } as const;
}

const styles = StyleSheet.create({
  h1: { fontSize: 28, fontWeight: "800" },
  cardTitle: { fontSize: 16, fontWeight: "700" },
  help: { fontSize: 13, marginTop: 6, lineHeight: 18 },
  statusRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginTop: 10,
    gap: 8,
  },
  envRow: { flexDirection: "row", gap: 10, marginTop: 12 },
  envChip: {
    flex: 1,
    height: 44,
    borderRadius: 10,
    borderWidth: StyleSheet.hairlineWidth,
    alignItems: "center",
    justifyContent: "center",
  },
});
