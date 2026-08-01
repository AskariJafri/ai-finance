import { useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import {
  useChatMessages,
  useChatSessions,
  useChatStatus,
  useDeleteChatSession,
  useSendChatMessage,
} from "../api/hooks";
import { ChatMessage, ChatSuggestion } from "../api/types";
import { Card } from "../components/Card";
import { useTheme } from "../theme";

export function ChatScreen() {
  const theme = useTheme();
  const status = useChatStatus();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [useMyData, setUseMyData] = useState(true);
  const [historyOpen, setHistoryOpen] = useState(false);

  const messages = useChatMessages(sessionId);
  const send = useSendChatMessage();
  const listRef = useRef<FlatList<ChatMessage>>(null);

  const thread = messages.data ?? [];
  const isEmpty = !sessionId || thread.length === 0;

  // The optimistic user bubble: shown while the model is still writing so the
  // question doesn't disappear into the input box.
  const pendingQuestion = send.isPending ? send.variables?.message : undefined;

  const rows = useMemo(() => {
    if (!pendingQuestion) return thread;
    return [
      ...thread,
      {
        id: "pending",
        session_id: sessionId ?? "",
        role: "user" as const,
        content: pendingQuestion,
        source: null,
        model: null,
        context_used: [],
        created_at: new Date().toISOString(),
      },
    ];
  }, [thread, pendingQuestion, sessionId]);

  useEffect(() => {
    if (rows.length) listRef.current?.scrollToEnd({ animated: true });
  }, [rows.length, send.isPending]);

  const submit = (text: string) => {
    const message = text.trim();
    if (!message || send.isPending) return;
    setDraft("");
    send.mutate(
      { message, session_id: sessionId, include_finances: useMyData },
      { onSuccess: (data) => setSessionId(data.session_id) },
    );
  };

  return (
    <SafeAreaView
      style={{ flex: 1, backgroundColor: theme.colors.background }}
      edges={["top"]}
    >
      <View style={styles.header}>
        <View style={{ flex: 1 }}>
          <Text style={[styles.h1, { color: theme.colors.text }]}>Ask</Text>
          <Text style={{ color: theme.colors.textMuted, fontSize: 12 }}>
            {status.data?.llm_available
              ? `Your money + trading, explained${
                  status.data.model ? ` · ${status.data.model}` : ""
                }`
              : "AI offline — definitions still work"}
          </Text>
        </View>
        <Pressable onPress={() => setHistoryOpen(true)} hitSlop={8}>
          <Text style={{ color: theme.colors.primary, fontSize: 14 }}>History</Text>
        </Pressable>
        {sessionId ? (
          <Pressable onPress={() => setSessionId(null)} hitSlop={8}>
            <Text style={{ color: theme.colors.primary, fontSize: 14 }}>New</Text>
          </Pressable>
        ) : null}
      </View>

      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        keyboardVerticalOffset={Platform.OS === "ios" ? 8 : 0}
      >
        {isEmpty && !send.isPending ? (
          <EmptyState
            suggestions={status.data?.suggestions ?? []}
            loading={status.isLoading}
            onPick={submit}
          />
        ) : (
          <FlatList
            ref={listRef}
            data={rows}
            keyExtractor={(m) => m.id}
            contentContainerStyle={{ padding: 16, gap: 12 }}
            renderItem={({ item }) => <Bubble message={item} />}
            ListFooterComponent={
              send.isPending ? <Thinking /> : send.isError ? <SendFailed /> : null
            }
            onContentSizeChange={() =>
              listRef.current?.scrollToEnd({ animated: true })
            }
          />
        )}

        <View
          style={[
            styles.composer,
            {
              backgroundColor: theme.colors.surface,
              borderTopColor: theme.colors.border,
            },
          ]}
        >
          <Pressable
            onPress={() => setUseMyData((v) => !v)}
            hitSlop={6}
            style={[
              styles.dataToggle,
              {
                backgroundColor: useMyData
                  ? theme.colors.primary
                  : theme.colors.surfaceAlt,
                borderRadius: theme.radius.sm,
              },
            ]}
          >
            <Text
              style={{
                fontSize: 11,
                fontWeight: "700",
                color: useMyData ? theme.colors.primaryText : theme.colors.textMuted,
              }}
            >
              {useMyData ? "MY DATA ON" : "MY DATA OFF"}
            </Text>
          </Pressable>
          <TextInput
            value={draft}
            onChangeText={setDraft}
            placeholder="Ask about your money or a trading term…"
            placeholderTextColor={theme.colors.textMuted}
            multiline
            onSubmitEditing={() => submit(draft)}
            style={[
              styles.input,
              {
                color: theme.colors.text,
                backgroundColor: theme.colors.surfaceAlt,
                borderRadius: theme.radius.md,
              },
            ]}
          />
          <Pressable
            onPress={() => submit(draft)}
            disabled={!draft.trim() || send.isPending}
            style={[
              styles.sendButton,
              {
                backgroundColor: theme.colors.primary,
                borderRadius: theme.radius.md,
                opacity: !draft.trim() || send.isPending ? 0.4 : 1,
              },
            ]}
          >
            <Text style={{ color: theme.colors.primaryText, fontWeight: "700" }}>
              ↑
            </Text>
          </Pressable>
        </View>
      </KeyboardAvoidingView>

      <HistoryModal
        visible={historyOpen}
        onClose={() => setHistoryOpen(false)}
        activeId={sessionId}
        onSelect={(id) => {
          setSessionId(id);
          setHistoryOpen(false);
        }}
      />
    </SafeAreaView>
  );
}

function Bubble({ message }: { message: ChatMessage }) {
  const theme = useTheme();
  const mine = message.role === "user";
  // Only the sections that grounded the answer, not the glossary internals.
  const grounded = message.context_used.filter((c) => !c.startsWith("glossary:"));

  return (
    <View style={{ alignItems: mine ? "flex-end" : "flex-start" }}>
      <View
        style={[
          styles.bubble,
          {
            backgroundColor: mine ? theme.colors.primary : theme.colors.surface,
            borderColor: theme.colors.border,
            borderWidth: mine ? 0 : StyleSheet.hairlineWidth,
            borderRadius: theme.radius.lg,
          },
        ]}
      >
        <Text
          style={{
            color: mine ? theme.colors.primaryText : theme.colors.text,
            fontSize: 15,
            lineHeight: 22,
          }}
        >
          {message.content}
        </Text>
      </View>

      {!mine && message.source === "glossary" ? (
        <Text style={[styles.meta, { color: theme.colors.textMuted }]}>
          AI offline — this is the app's own written explainer.
        </Text>
      ) : null}
      {!mine && grounded.length ? (
        <Text style={[styles.meta, { color: theme.colors.textMuted }]}>
          Answered using your {grounded.join(", ")} data.
        </Text>
      ) : null}
    </View>
  );
}

function Thinking() {
  const theme = useTheme();
  return (
    <View style={styles.thinking}>
      <ActivityIndicator color={theme.colors.primary} />
      <Text style={{ color: theme.colors.textMuted, fontSize: 13 }}>
        Thinking — a local model can take a minute…
      </Text>
    </View>
  );
}

function SendFailed() {
  const theme = useTheme();
  return (
    <Text style={[styles.meta, { color: theme.colors.negative, marginTop: 4 }]}>
      Couldn't reach the server. Check the API address in Settings and try again.
    </Text>
  );
}

function EmptyState({
  suggestions,
  loading,
  onPick,
}: {
  suggestions: ChatSuggestion[];
  loading: boolean;
  onPick: (prompt: string) => void;
}) {
  const theme = useTheme();
  const groups: { key: ChatSuggestion["group"]; title: string }[] = [
    { key: "money", title: "Your money" },
    { key: "invest", title: "Your investments" },
    { key: "learn", title: "Learn the terms" },
  ];

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={theme.colors.primary} />
      </View>
    );
  }

  return (
    <ScrollView contentContainerStyle={{ padding: 16, gap: 14 }}>
      <Text style={{ color: theme.colors.textMuted, lineHeight: 20 }}>
        Ask about your own accounts, spending and portfolio — every figure comes
        from your real data, never guessed — or ask what any trading term means.
      </Text>
      {groups.map((g) => {
        const items = suggestions.filter((s) => s.group === g.key);
        if (!items.length) return null;
        return (
          <Card key={g.key}>
            <Text style={[styles.cardTitle, { color: theme.colors.text }]}>
              {g.title}
            </Text>
            <View style={{ gap: 8, marginTop: 10 }}>
              {items.map((s) => (
                <Pressable
                  key={s.label}
                  onPress={() => onPick(s.prompt)}
                  style={({ pressed }) => [
                    styles.suggestion,
                    {
                      backgroundColor: theme.colors.surfaceAlt,
                      borderRadius: theme.radius.md,
                      opacity: pressed ? 0.7 : 1,
                    },
                  ]}
                >
                  <Text style={{ color: theme.colors.text, fontSize: 14 }}>
                    {s.label}
                  </Text>
                </Pressable>
              ))}
            </View>
          </Card>
        );
      })}
    </ScrollView>
  );
}

function HistoryModal({
  visible,
  onClose,
  activeId,
  onSelect,
}: {
  visible: boolean;
  onClose: () => void;
  activeId: string | null;
  onSelect: (id: string) => void;
}) {
  const theme = useTheme();
  const sessions = useChatSessions();
  const remove = useDeleteChatSession();

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose}>
        <Pressable style={{ width: "100%" }} onPress={() => {}}>
          <Card style={{ margin: 12, maxHeight: 420 }}>
            <Text style={[styles.cardTitle, { color: theme.colors.text }]}>
              Past chats
            </Text>
            {sessions.isLoading ? (
              <ActivityIndicator
                color={theme.colors.primary}
                style={{ marginTop: 16 }}
              />
            ) : !sessions.data?.length ? (
              <Text style={{ color: theme.colors.textMuted, marginTop: 10 }}>
                No chats yet.
              </Text>
            ) : (
              <ScrollView style={{ marginTop: 10 }}>
                {sessions.data.map((s) => (
                  <View key={s.id} style={styles.historyRow}>
                    <Pressable
                      style={{ flex: 1 }}
                      onPress={() => onSelect(s.id)}
                      hitSlop={6}
                    >
                      <Text
                        numberOfLines={1}
                        style={{
                          color:
                            s.id === activeId
                              ? theme.colors.primary
                              : theme.colors.text,
                          fontSize: 14,
                          fontWeight: s.id === activeId ? "700" : "400",
                        }}
                      >
                        {s.title}
                      </Text>
                      <Text style={{ color: theme.colors.textMuted, fontSize: 11 }}>
                        {s.message_count} messages ·{" "}
                        {new Date(s.updated_at).toLocaleDateString()}
                      </Text>
                    </Pressable>
                    <Pressable onPress={() => remove.mutate(s.id)} hitSlop={8}>
                      <Text style={{ color: theme.colors.danger, fontSize: 13 }}>
                        Delete
                      </Text>
                    </Pressable>
                  </View>
                ))}
              </ScrollView>
            )}
          </Card>
        </Pressable>
      </Pressable>
    </Modal>
  );
}

const styles = StyleSheet.create({
  header: {
    flexDirection: "row",
    alignItems: "center",
    gap: 16,
    paddingHorizontal: 16,
    paddingVertical: 10,
  },
  h1: { fontSize: 28, fontWeight: "800" },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24 },
  cardTitle: { fontSize: 16, fontWeight: "700" },
  bubble: { maxWidth: "88%", paddingHorizontal: 14, paddingVertical: 10 },
  meta: { fontSize: 11, marginTop: 4, marginHorizontal: 6, fontStyle: "italic" },
  thinking: { flexDirection: "row", alignItems: "center", gap: 10, paddingVertical: 8 },
  suggestion: { paddingHorizontal: 12, paddingVertical: 11 },
  composer: {
    flexDirection: "row",
    alignItems: "flex-end",
    gap: 8,
    padding: 10,
    borderTopWidth: StyleSheet.hairlineWidth,
  },
  dataToggle: { paddingHorizontal: 8, paddingVertical: 12 },
  input: {
    flex: 1,
    maxHeight: 120,
    minHeight: 44,
    paddingHorizontal: 14,
    paddingTop: 12,
    paddingBottom: 12,
    fontSize: 15,
  },
  sendButton: {
    width: 44,
    height: 44,
    alignItems: "center",
    justifyContent: "center",
  },
  backdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.45)",
    justifyContent: "flex-end",
  },
  historyRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingVertical: 10,
  },
});
