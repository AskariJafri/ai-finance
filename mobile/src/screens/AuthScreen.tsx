import { useEffect, useState } from "react";
import { Controller, useForm } from "react-hook-form";
import {
  Alert,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { useLogin, useRegister } from "../api/hooks";
import { Button } from "../components/Button";
import { useAppConfig } from "../stores/appConfig";
import { useTheme } from "../theme";

type FormValues = { email: string; password: string; full_name?: string };

export function AuthScreen() {
  const theme = useTheme();
  const [mode, setMode] = useState<"login" | "register">("login");
  const login = useLogin();
  const register = useRegister();
  const pending = login.isPending || register.isPending;
  const errorMsg =
    (login.error as Error | null)?.message || (register.error as Error | null)?.message;

  // Server URL is configurable here (pre-login) so the app can be pointed at any
  // backend — LAN IP, tunnel, deployed — without a rebuild. Mirrors Settings.
  const baseUrl = useAppConfig((s) => s.baseUrl);
  const setBaseUrl = useAppConfig((s) => s.setBaseUrl);
  const [showServer, setShowServer] = useState(false);
  const [serverDraft, setServerDraft] = useState(baseUrl);
  useEffect(() => setServerDraft(baseUrl), [baseUrl]);

  const onSaveServer = async () => {
    await setBaseUrl(serverDraft);
    Alert.alert("Saved", "Server URL updated.");
  };

  const { control, handleSubmit } = useForm<FormValues>({
    defaultValues: { email: "", password: "", full_name: "" },
  });

  const onSubmit = (values: FormValues) => {
    if (mode === "login") {
      login.mutate({ email: values.email.trim(), password: values.password });
    } else {
      register.mutate({
        email: values.email.trim(),
        password: values.password,
        full_name: values.full_name?.trim() || undefined,
      });
    }
  };

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.colors.background }}>
      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
          <Text style={[styles.brand, { color: theme.colors.primary }]}>AI Finance</Text>
          <Text style={[styles.subtitle, { color: theme.colors.textMuted }]}>
            {mode === "login" ? "Welcome back." : "Create your account."}
          </Text>

          {mode === "register" && (
            <Controller
              control={control}
              name="full_name"
              render={({ field: { onChange, value } }) => (
                <TextInput
                  placeholder="Full name (optional)"
                  placeholderTextColor={theme.colors.textMuted}
                  value={value}
                  onChangeText={onChange}
                  style={fieldStyle(theme)}
                />
              )}
            />
          )}

          <Controller
            control={control}
            name="email"
            rules={{ required: true }}
            render={({ field: { onChange, value } }) => (
              <TextInput
                placeholder="Email"
                placeholderTextColor={theme.colors.textMuted}
                autoCapitalize="none"
                keyboardType="email-address"
                value={value}
                onChangeText={onChange}
                style={fieldStyle(theme)}
              />
            )}
          />

          <Controller
            control={control}
            name="password"
            rules={{ required: true, minLength: 8 }}
            render={({ field: { onChange, value } }) => (
              <TextInput
                placeholder="Password (min 8 chars)"
                placeholderTextColor={theme.colors.textMuted}
                secureTextEntry
                value={value}
                onChangeText={onChange}
                style={fieldStyle(theme)}
              />
            )}
          />

          {errorMsg ? (
            <Text style={[styles.error, { color: theme.colors.danger }]}>
              {mode === "login"
                ? "Login failed. Check your credentials."
                : "Could not register. Try a different email."}
            </Text>
          ) : null}

          <Button
            title={mode === "login" ? "Log in" : "Sign up"}
            onPress={handleSubmit(onSubmit)}
            loading={pending}
            style={{ marginTop: 8 }}
          />

          <Text
            onPress={() => setMode(mode === "login" ? "register" : "login")}
            style={[styles.switch, { color: theme.colors.primary }]}
          >
            {mode === "login"
              ? "New here? Create an account"
              : "Already have an account? Log in"}
          </Text>

          <Text
            onPress={() => setShowServer((v) => !v)}
            style={[styles.serverToggle, { color: theme.colors.textMuted }]}
          >
            {showServer ? "Hide server settings" : "Can't connect? Server settings"}
          </Text>
          {showServer && (
            <View style={{ marginTop: 8 }}>
              <TextInput
                value={serverDraft}
                onChangeText={setServerDraft}
                autoCapitalize="none"
                keyboardType="url"
                placeholder="http://192.168.x.x:8010/api/v1"
                placeholderTextColor={theme.colors.textMuted}
                style={fieldStyle(theme)}
              />
              <Button
                title="Save server URL"
                variant="secondary"
                onPress={onSaveServer}
                style={{ marginTop: 10 }}
              />
            </View>
          )}
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

function fieldStyle(theme: ReturnType<typeof useTheme>) {
  return {
    height: 52,
    borderRadius: theme.radius.md,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: theme.colors.border,
    backgroundColor: theme.colors.surface,
    color: theme.colors.text,
    paddingHorizontal: 16,
    fontSize: 16,
    marginTop: 12,
  } as const;
}

const styles = StyleSheet.create({
  container: { padding: 24, justifyContent: "center", flexGrow: 1 },
  brand: { fontSize: 34, fontWeight: "800" },
  subtitle: { fontSize: 16, marginTop: 6, marginBottom: 12 },
  error: { marginTop: 12, fontSize: 14 },
  switch: { marginTop: 20, textAlign: "center", fontSize: 15, fontWeight: "600" },
  serverToggle: { marginTop: 24, textAlign: "center", fontSize: 13 },
});
