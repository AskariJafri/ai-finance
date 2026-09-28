import { useMemo, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import {
  useCategories,
  useCreateCategory,
  useDeleteCategory,
  useUpdateCategoryDefinition,
} from "../api/hooks";
import { Card } from "../components/Card";
import { Category } from "../api/types";
import { alert } from "../lib/alert";
import { useTheme } from "../theme";
import { CUSTOM_CATEGORY_PALETTE } from "../utils/categoryPalette";

export function ManageCategoriesScreen() {
  const theme = useTheme();
  const { data, isLoading } = useCategories();
  const updateCategory = useUpdateCategoryDefinition();
  const deleteCategory = useDeleteCategory();
  const createCategory = useCreateCategory();
  const [editing, setEditing] = useState<Category | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [name, setName] = useState("");
  const [bucket, setBucket] = useState<"needs" | "wants">("wants");
  const [color, setColor] = useState(CUSTOM_CATEGORY_PALETTE[0]);

  const categories = data ?? [];
  const income = useMemo(() => categories.filter((c) => c.group === "income"), [categories]);
  const expense = useMemo(() => categories.filter((c) => c.group === "expense"), [categories]);

  const startEditing = (c: Category) => {
    setIsNew(false);
    setEditing(c);
    setName(c.name);
    setBucket(c.budget_bucket === "needs" ? "needs" : "wants");
    setColor(c.color ?? CUSTOM_CATEGORY_PALETTE[0]);
  };

  const startCreating = () => {
    setIsNew(true);
    setEditing(null);
    setName("");
    setBucket("wants");
    setColor(CUSTOM_CATEGORY_PALETTE[0]);
  };

  const closeSheet = () => {
    setEditing(null);
    setIsNew(false);
  };

  const saveEdit = () => {
    const trimmed = name.trim();
    if (!trimmed) return;

    if (isNew) {
      createCategory.mutate(
        { name: trimmed, group: "expense", color, budget_bucket: bucket },
        { onSuccess: closeSheet },
      );
      return;
    }
    if (!editing) return;
    updateCategory.mutate(
      {
        categoryId: editing.id,
        name: trimmed,
        color,
        ...(editing.group === "expense" ? { budget_bucket: bucket } : {}),
      },
      { onSuccess: closeSheet },
    );
  };

  const confirmDelete = (c: Category) => {
    alert(
      `Delete "${c.name}"?`,
      "Every transaction in this category moves to Uncategorized. This can't be undone.",
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Delete",
          style: "destructive",
          onPress: () => deleteCategory.mutate(c.id),
        },
      ],
    );
  };

  const onLongPress = (c: Category) => {
    if (c.is_system) return;
    alert(c.name, undefined, [
      { text: "Cancel", style: "cancel" },
      { text: "Edit", onPress: () => startEditing(c) },
      { text: "Delete", style: "destructive", onPress: () => confirmDelete(c) },
    ]);
  };

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.colors.background }} edges={["bottom"]}>
      <ScrollView contentContainerStyle={{ padding: 16, gap: 14 }}>
        {isLoading ? null : (
          <>
            <Pressable
              onPress={startCreating}
              style={({ pressed }) => [
                styles.newButton,
                { borderColor: theme.colors.primary, opacity: pressed ? 0.6 : 1 },
              ]}
            >
              <Text style={{ color: theme.colors.primary, fontWeight: "700", fontSize: 15 }}>
                + New category
              </Text>
            </Pressable>
            <Section title="Expense" items={expense} onLongPress={onLongPress} theme={theme} />
            <Section title="Income" items={income} onLongPress={onLongPress} theme={theme} />
            <Text style={{ color: theme.colors.textMuted, fontSize: 12, textAlign: "center" }}>
              Long-press a category you created to edit or delete it. Built-in
              categories are read-only.
            </Text>
          </>
        )}
      </ScrollView>

      {editing || isNew ? (
        <View style={[styles.editSheet, { backgroundColor: theme.colors.background }]}>
          <Card>
            <Text style={[styles.title, { color: theme.colors.text }]}>
              {isNew ? "New category" : "Edit category"}
            </Text>
            <TextInput
              value={name}
              onChangeText={setName}
              style={[styles.input, { color: theme.colors.text, borderColor: theme.colors.border }]}
              placeholder="Name"
              placeholderTextColor={theme.colors.textMuted}
            />
            {isNew || editing?.group === "expense" ? (
              <View style={styles.bucketRow}>
                <BucketChip
                  label="Essential (needs)"
                  active={bucket === "needs"}
                  onPress={() => setBucket("needs")}
                  theme={theme}
                />
                <BucketChip
                  label="Lifestyle (wants)"
                  active={bucket === "wants"}
                  onPress={() => setBucket("wants")}
                  theme={theme}
                />
              </View>
            ) : null}
            <View style={styles.swatchRow}>
              {CUSTOM_CATEGORY_PALETTE.map((c) => (
                <Pressable
                  key={c}
                  onPress={() => setColor(c)}
                  style={[
                    styles.swatch,
                    { backgroundColor: c },
                    color === c ? { borderColor: theme.colors.text, borderWidth: 2 } : null,
                  ]}
                />
              ))}
            </View>
            <View style={{ flexDirection: "row", gap: 10 }}>
              <Pressable
                onPress={closeSheet}
                style={[styles.button, { backgroundColor: theme.colors.surfaceAlt, flex: 1 }]}
              >
                <Text style={{ color: theme.colors.text, fontWeight: "700" }}>Cancel</Text>
              </Pressable>
              <Pressable
                onPress={saveEdit}
                disabled={!name.trim() || updateCategory.isPending || createCategory.isPending}
                style={[
                  styles.button,
                  {
                    backgroundColor: theme.colors.primary,
                    flex: 1,
                    opacity:
                      !name.trim() || updateCategory.isPending || createCategory.isPending
                        ? 0.5
                        : 1,
                  },
                ]}
              >
                <Text style={{ color: theme.colors.primaryText, fontWeight: "700" }}>
                  {isNew ? "Create" : "Save"}
                </Text>
              </Pressable>
            </View>
          </Card>
        </View>
      ) : null}
    </SafeAreaView>
  );
}

function Section({
  title,
  items,
  onLongPress,
  theme,
}: {
  title: string;
  items: Category[];
  onLongPress: (c: Category) => void;
  theme: ReturnType<typeof useTheme>;
}) {
  if (items.length === 0) return null;
  return (
    <Card>
      <Text style={[styles.title, { color: theme.colors.text }]}>{title}</Text>
      {items.map((c) => (
        <Pressable
          key={c.id}
          onLongPress={() => onLongPress(c)}
          delayLongPress={300}
          style={({ pressed }) => [
            styles.row,
            { borderBottomColor: theme.colors.border, opacity: pressed ? 0.6 : 1 },
          ]}
        >
          <View style={[styles.dot, { backgroundColor: c.color ?? theme.colors.primary }]} />
          <Text style={{ flex: 1, color: theme.colors.text, fontSize: 15 }}>{c.name}</Text>
          {c.is_system ? (
            <Text style={{ color: theme.colors.textMuted, fontSize: 12 }}>Built-in</Text>
          ) : (
            <Text style={{ color: theme.colors.textMuted, fontSize: 12 }}>
              {c.budget_bucket === "needs" ? "Essential" : c.budget_bucket === "wants" ? "Lifestyle" : ""}
            </Text>
          )}
        </Pressable>
      ))}
    </Card>
  );
}

function BucketChip({
  label,
  active,
  onPress,
  theme,
}: {
  label: string;
  active: boolean;
  onPress: () => void;
  theme: ReturnType<typeof useTheme>;
}) {
  return (
    <Pressable
      onPress={onPress}
      style={[
        styles.bucketChip,
        {
          backgroundColor: active ? theme.colors.primary : theme.colors.surfaceAlt,
          borderColor: theme.colors.border,
        },
      ]}
    >
      <Text style={{ color: active ? theme.colors.primaryText : theme.colors.text, fontSize: 13, fontWeight: "600" }}>
        {label}
      </Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  title: { fontSize: 16, fontWeight: "700", marginBottom: 6 },
  newButton: {
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 10,
    paddingVertical: 14,
    alignItems: "center",
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingVertical: 12,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  dot: { width: 12, height: 12, borderRadius: 6 },
  editSheet: {
    position: "absolute",
    left: 0,
    right: 0,
    bottom: 0,
    padding: 12,
  },
  input: {
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 16,
    marginBottom: 12,
  },
  bucketRow: { flexDirection: "row", gap: 8, marginBottom: 12 },
  bucketChip: {
    paddingHorizontal: 12,
    height: 34,
    borderRadius: 17,
    borderWidth: StyleSheet.hairlineWidth,
    alignItems: "center",
    justifyContent: "center",
  },
  swatchRow: { flexDirection: "row", flexWrap: "wrap", gap: 10, marginBottom: 16 },
  swatch: { width: 28, height: 28, borderRadius: 14 },
  button: {
    height: 44,
    borderRadius: 10,
    alignItems: "center",
    justifyContent: "center",
  },
});
