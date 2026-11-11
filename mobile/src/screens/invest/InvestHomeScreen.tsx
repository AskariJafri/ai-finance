import { useNavigation } from "@react-navigation/native";
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import {
  useBrokerageConnections,
  useInvestSync,
  useLatestActionRun,
  usePortfolio,
  useSyncBrokerage,
} from "../../api/hooks";
import { Button } from "../../components/Button";
import { Card } from "../../components/Card";
import { InvestAiCard } from "../../components/InvestAiCard";
import { PnlText } from "../../components/PnlText";
import type { BrokerageConnection } from "../../api/types";
import { useTheme } from "../../theme";
import { formatCurrency, formatDate, formatPercent } from "../../utils/format";

/** "3 minutes ago" — a sync time is only ever read as "how stale is this". */
function sinceLabel(iso: string | null): string {
  if (!iso) return "never synced";
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms)) return "never synced";
  const mins = Math.floor(ms / 60000);
  if (mins < 1) return "synced just now";
  if (mins < 60) return `synced ${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `synced ${hours}h ago`;
  return `synced ${Math.floor(hours / 24)}d ago`;
}

type NavItem = { screen: string; title: string; subtitle: string };
type Tile = { screen: string; title: string; glyph: string };

/**
 * Three tiers, because eleven equally-weighted cards read as a wall rather
 * than a menu:
 *
 * * **Primary** — the two things you actually open this tab to do. Full width,
 *   with a sentence explaining them.
 * * **Tools** — everything else, as a two-column grid of labelled tiles. Same
 *   destinations, roughly a third of the scroll, scannable as a set.
 * * **Practice** — the simulated account, kept visually apart so a paper trade
 *   can never be mistaken for a real one.
 */
const PRIMARY: NavItem[] = [
  {
    screen: "Actions",
    title: "What should I do?",
    subtitle: "AI verdict on every holding — what to sell, what to buy",
  },
  {
    screen: "DeployCash",
    title: "Invest Cash",
    subtitle: "Turn an amount into a concrete buy plan",
  },
];

const TOOLS: Tile[] = [
  { screen: "Committee", title: "AI Committee", glyph: "⚖" },
  { screen: "AutoTrader", title: "Auto-Trader", glyph: "⟳" },
  { screen: "Watchlist", title: "Watchlist", glyph: "☆" },
  { screen: "Holdings", title: "Holdings", glyph: "▤" },
  { screen: "Rebalance", title: "Rebalance", glyph: "⇄" },
  { screen: "Risk", title: "Risk", glyph: "⚠" },
  { screen: "Projections", title: "Projections", glyph: "↗" },
  { screen: "Scenarios", title: "Stress Test", glyph: "⚡" },
  { screen: "Backtest", title: "Backtest", glyph: "⏱" },
];

// The subset that says something useful with an empty portfolio. Offering
// risk analysis on nothing is just a dead end with a chevron on it.
const TOOLS_EMPTY: Tile[] = TOOLS.filter((t) =>
  ["Committee", "Watchlist"].includes(t.screen),
);

export function InvestHomeScreen() {
  const theme = useTheme();
  const navigation = useNavigation<any>();
  const { data, isLoading, refetch, isRefetching } = usePortfolio();
  const sync = useInvestSync();
  const connections = useBrokerageConnections();
  const syncBrokerage = useSyncBrokerage();
  const linked = connections.data ?? [];
  // The plan builds in the background; say so here, so it is clear the work
  // carried on after leaving that screen.
  const planRun = useLatestActionRun().data;
  const primary = PRIMARY.map((item) =>
    item.screen !== "Actions" || !planRun
      ? item
      : planRun.status === "running"
        ? { ...item, subtitle: "Building your plan… tap to watch" }
        : planRun.status === "done" && planRun.result
          ? {
              ...item,
              subtitle: planRun.result.actions.length
                ? `Plan ready: ${planRun.result.actions.length} trade${planRun.result.actions.length === 1 ? "" : "s"} suggested`
                : "Plan ready: no changes suggested",
            }
          : item,
  );

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.colors.background }} edges={["top"]}>
      <View style={styles.header}>
        <Text style={[styles.h1, { color: theme.colors.text }]}>Invest</Text>
        {data?.connected ? (
          <Pressable onPress={() => sync.mutate()} hitSlop={8}>
            <Text style={{ color: theme.colors.primary, fontWeight: "600" }}>
              {sync.isPending ? "Syncing…" : "Sync data"}
            </Text>
          </Pressable>
        ) : null}
      </View>

      {isLoading ? (
        <View style={styles.center}>
          <ActivityIndicator color={theme.colors.primary} />
        </View>
      ) : !data?.connected ? (
        <ScrollView contentContainerStyle={{ padding: 16, gap: 14 }}>
          {linked.length > 0 ? (
            // A linked account with nothing in it here means the mirror has not
            // run — say that, rather than asking for the holdings the broker
            // already knows about.
            <Card>
              <Text style={[styles.cardTitle, { color: theme.colors.text }]}>
                Pull in your {linked[0]?.institution_name ?? "brokerage"} positions
              </Text>
              <Text style={[styles.body, { color: theme.colors.textMuted }]}>
                Your account is linked but nothing has been mirrored across yet.
                Syncing copies your positions in and unlocks risk analysis,
                stress tests, projections and rebalancing on them.
              </Text>
              <Button
                title={syncBrokerage.isPending ? "Syncing…" : "Sync positions"}
                onPress={() => syncBrokerage.mutate()}
                loading={syncBrokerage.isPending}
                style={{ marginTop: 12 }}
              />
              <Button
                title="Enter holdings by hand"
                variant="secondary"
                onPress={() => navigation.navigate("Holdings")}
                style={{ marginTop: 8 }}
              />
            </Card>
          ) : (
            <Card>
              <Text style={[styles.cardTitle, { color: theme.colors.text }]}>
                Add your holdings to begin
              </Text>
              <Text style={[styles.body, { color: theme.colors.textMuted }]}>
                Link your brokerage in Settings and your positions come across on
                their own — or tell the app what you own at your broker
                (Wealthsimple, Questrade, TD, wherever) by hand. Either way it
                does the rest: risk analysis, stress tests against 2008 and
                COVID, Monte Carlo projections, and rebalancing suggestions.
                {"\n\n"}Prices come from public market data, so Canadian listings
                work natively (VFV.TO, XEQT.TO) alongside US ones (SPY, AAPL),
                with everything converted to CAD.
                {"\n\n"}Nothing here places trades. The app tells you what to
                trade; you place it at your broker and tap "Done".
              </Text>
              <Button
                title="Add holdings"
                onPress={() => navigation.navigate("Holdings")}
                style={{ marginTop: 12 }}
              />
              <Button
                title="Link a brokerage"
                variant="secondary"
                onPress={() => navigation.navigate("Settings")}
                style={{ marginTop: 8 }}
              />
            </Card>
          )}

          <PrimaryActions items={primary} onNavigate={(s) => navigation.navigate(s)} />
          <ToolGrid
            title="Research"
            items={TOOLS_EMPTY}
            onNavigate={(s) => navigation.navigate(s)}
          />
          <PracticeRow onNavigate={(s) => navigation.navigate(s)} />
        </ScrollView>
      ) : (
        <ScrollView
          contentContainerStyle={{ padding: 16, gap: 14 }}
          refreshControl={
            <RefreshControl
              refreshing={isRefetching}
              onRefresh={refetch}
              tintColor={theme.colors.primary}
            />
          }
        >
          <Card>
            <Text style={[styles.label, { color: theme.colors.textMuted }]}>
              Portfolio value (CAD)
            </Text>
            <Text style={[styles.hero, { color: theme.colors.text }]}>
              {formatCurrency(data.account?.equity ?? 0)}
            </Text>
            <Text style={[styles.subtle, { color: theme.colors.textMuted }]}>
              {formatCurrency(data.account?.invested ?? 0)} invested ·{" "}
              {formatCurrency(data.account?.cash ?? 0)} cash
              {data.usd_cad_rate ? ` · USD/CAD ${data.usd_cad_rate.toFixed(2)}` : ""}
            </Text>

            <View style={[styles.pnlRow, { borderTopColor: theme.colors.surfaceAlt }]}>
              <View style={{ flex: 1 }}>
                <Text style={[styles.label, { color: theme.colors.textMuted }]}>
                  Unrealized P/L
                </Text>
                <PnlText
                  amount={data.account?.unrealized_pl}
                  pct={data.account?.unrealized_pl_pct}
                  style={styles.pnlValue}
                />
                <Text style={[styles.pnlNote, { color: theme.colors.textMuted }]}>
                  on {formatCurrency(data.account?.cost_basis ?? 0)} cost
                </Text>
              </View>
              <View style={{ flex: 1 }}>
                <Text style={[styles.label, { color: theme.colors.textMuted }]}>
                  {data.prices_as_of ? `Day (${formatDate(data.prices_as_of)})` : "Day"}
                </Text>
                <PnlText
                  amount={data.account?.day_pl}
                  pct={data.account?.day_pl_pct}
                  placeholder="No prior close"
                  style={styles.pnlValue}
                />
                <Text style={[styles.pnlNote, { color: theme.colors.textMuted }]}>
                  vs previous close
                </Text>
              </View>
            </View>
            {data.stale_symbols.length > 0 ? (
              <Text style={{ color: theme.colors.danger, fontSize: 12, marginTop: 8 }}>
                No price data for {data.stale_symbols.join(", ")} — tap Sync data.
              </Text>
            ) : null}
          </Card>

          {linked.length > 0 ? (
            <BrokerageCard
              connections={linked}
              onSync={() => syncBrokerage.mutate()}
              syncing={syncBrokerage.isPending}
            />
          ) : null}

          <Card>
            <Text style={[styles.cardTitle, { color: theme.colors.text }]}>Positions</Text>
            {data.positions.map((p) => (
              <View key={p.symbol} style={styles.positionRow}>
                <View style={{ flex: 1 }}>
                  <Text style={[styles.symbol, { color: theme.colors.text }]}>
                    {p.symbol}
                    <Text style={{ color: theme.colors.textMuted, fontWeight: "400", fontSize: 12 }}>
                      {"  "}
                      {formatPercent(p.weight)}
                      {p.is_synced ? "  · from your broker" : ""}
                    </Text>
                  </Text>
                  <View style={[styles.weightTrack, { backgroundColor: theme.colors.surfaceAlt }]}>
                    <View
                      style={[
                        styles.weightFill,
                        { width: `${p.weight * 100}%`, backgroundColor: theme.colors.primary },
                      ]}
                    />
                  </View>
                </View>
                <View style={{ alignItems: "flex-end" }}>
                  <Text style={{ color: theme.colors.text, fontWeight: "600" }}>
                    {formatCurrency(p.market_value)}
                  </Text>
                  <PnlText
                    amount={p.unrealized_pl}
                    pct={p.unrealized_pl_pct}
                    style={{ fontSize: 12 }}
                  />
                  <Text style={{ color: theme.colors.textMuted, fontSize: 11 }}>
                    day <PnlText amount={p.day_pl} pct={p.day_change_pct} style={{ fontSize: 11 }} />
                  </Text>
                </View>
              </View>
            ))}
          </Card>

          <PrimaryActions items={primary} onNavigate={(s) => navigation.navigate(s)} />
          <ToolGrid title="Tools" items={TOOLS} onNavigate={(s) => navigation.navigate(s)} />
          <PracticeRow onNavigate={(s) => navigation.navigate(s)} />

          <Card>
            <Text style={[styles.cardTitle, { color: theme.colors.text, marginBottom: 10 }]}>
              AI health check
            </Text>
            <InvestAiCard />
          </Card>

          <Text style={[styles.disclaimer, { color: theme.colors.textMuted }]}>
            {data.disclaimer}
          </Text>
        </ScrollView>
      )}
    </SafeAreaView>
  );
}

function BrokerageCard({
  connections,
  onSync,
  syncing,
}: {
  connections: BrokerageConnection[];
  onSync: () => void;
  syncing: boolean;
}) {
  const theme = useTheme();
  const newest = connections
    .map((c) => c.last_synced_at)
    .filter((v): v is string => !!v)
    .sort()
    .pop();
  return (
    <Card>
      <View style={styles.navRow}>
        <Text style={[styles.cardTitle, { color: theme.colors.text, flex: 1 }]}>
          Linked brokerage
        </Text>
        <Pressable onPress={onSync} hitSlop={8} disabled={syncing}>
          <Text style={{ color: theme.colors.primary, fontWeight: "600" }}>
            {syncing ? "Syncing…" : "Sync positions"}
          </Text>
        </Pressable>
      </View>
      {connections.map((c) => (
        <View key={c.id} style={{ marginTop: 10 }}>
          <Text style={{ color: theme.colors.text, fontWeight: "600" }}>
            {c.institution_name ?? "Brokerage"} · {c.account_name ?? "Account"}
            {c.number_masked ? ` ${c.number_masked}` : ""}
          </Text>
          <Text style={{ color: theme.colors.textMuted, fontSize: 13, marginTop: 2 }}>
            {formatCurrency(c.total_value)} · {formatCurrency(c.cash)} cash ·{" "}
            {c.currency}
          </Text>
          <Text style={{ color: theme.colors.textMuted, fontSize: 12, marginTop: 2 }}>
            {c.trading_enabled
              ? "Trading enabled — orders you approve are sent here."
              : "Read-only — positions sync, orders can't be placed."}
          </Text>
        </View>
      ))}
      <Text style={{ color: theme.colors.textMuted, fontSize: 12, marginTop: 10 }}>
        {sinceLabel(newest ?? null)}
      </Text>
    </Card>
  );
}

/** The two headline actions: full width, explained, visually the loudest thing
 *  under the portfolio value. */
function PrimaryActions({
  items,
  onNavigate,
}: {
  items: NavItem[];
  onNavigate: (screen: string) => void;
}) {
  const theme = useTheme();
  return (
    <View style={{ gap: 10 }}>
      {items.map((c) => (
        <Pressable key={c.screen} onPress={() => onNavigate(c.screen)}>
          <Card style={{ borderColor: theme.colors.primary, borderWidth: 1 }}>
            <View style={styles.navRow}>
              <View style={{ flex: 1 }}>
                <Text style={[styles.cardTitle, { color: theme.colors.text }]}>{c.title}</Text>
                <Text style={{ color: theme.colors.textMuted, fontSize: 13, marginTop: 2 }}>
                  {c.subtitle}
                </Text>
              </View>
              <Text style={{ color: theme.colors.primary, fontSize: 20 }}>›</Text>
            </View>
          </Card>
        </Pressable>
      ))}
    </View>
  );
}

/** Everything else, two per row. A tile is a glyph and a label — the subtitle
 *  each of these used to carry is what made the old list a wall of text. */
function ToolGrid({
  title,
  items,
  onNavigate,
}: {
  title: string;
  items: Tile[];
  onNavigate: (screen: string) => void;
}) {
  const theme = useTheme();
  return (
    <View style={{ gap: 10 }}>
      <Text style={[styles.sectionTitle, { color: theme.colors.textMuted }]}>{title}</Text>
      <View style={styles.grid}>
        {items.map((t) => (
          <Pressable
            key={t.screen}
            onPress={() => onNavigate(t.screen)}
            style={styles.tileWrap}
            accessibilityRole="button"
            accessibilityLabel={t.title}
          >
            <View
              style={[
                styles.tile,
                {
                  backgroundColor: theme.colors.surface,
                  borderColor: theme.colors.border,
                  borderRadius: theme.radius.lg,
                },
              ]}
            >
              <Text style={{ color: theme.colors.primary, fontSize: 20 }}>{t.glyph}</Text>
              <Text
                numberOfLines={1}
                style={{ color: theme.colors.text, fontSize: 14, fontWeight: "600" }}
              >
                {t.title}
              </Text>
            </View>
          </Pressable>
        ))}
      </View>
    </View>
  );
}

/** Deliberately unlike everything above it: dashed, muted, and labelled as
 *  simulated, so it never blends into the real-money surface. */
function PracticeRow({ onNavigate }: { onNavigate: (screen: string) => void }) {
  const theme = useTheme();
  return (
    <Pressable onPress={() => onNavigate("Paper")}>
      <View
        style={[
          styles.practice,
          { borderColor: theme.colors.border, borderRadius: theme.radius.lg },
        ]}
      >
        <Text style={{ color: theme.colors.textMuted, fontSize: 18 }}>◇</Text>
        <View style={{ flex: 1 }}>
          <Text style={{ color: theme.colors.text, fontSize: 15, fontWeight: "600" }}>
            Practice trading
          </Text>
          <Text style={{ color: theme.colors.textMuted, fontSize: 12, marginTop: 1 }}>
            Simulated money — nothing here touches your broker
          </Text>
        </View>
        <Text style={{ color: theme.colors.textMuted, fontSize: 20 }}>›</Text>
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  sectionTitle: {
    fontSize: 12,
    fontWeight: "700",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    marginTop: 4,
  },
  header: {
    paddingHorizontal: 16,
    paddingVertical: 12,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  h1: { fontSize: 28, fontWeight: "800" },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  cardTitle: { fontSize: 16, fontWeight: "700" },
  body: { fontSize: 14, lineHeight: 21, marginTop: 8 },
  label: { fontSize: 13 },
  hero: { fontSize: 34, fontWeight: "800", marginTop: 4 },
  subtle: { fontSize: 13, marginTop: 4 },
  pnlRow: {
    flexDirection: "row",
    gap: 12,
    marginTop: 14,
    paddingTop: 12,
    borderTopWidth: StyleSheet.hairlineWidth,
  },
  pnlValue: { fontSize: 17, fontWeight: "700", marginTop: 3 },
  pnlNote: { fontSize: 11, marginTop: 2 },
  positionRow: { flexDirection: "row", alignItems: "center", gap: 12, marginTop: 12 },
  symbol: { fontSize: 15, fontWeight: "700" },
  weightTrack: { height: 6, borderRadius: 3, marginTop: 6, overflow: "hidden" },
  weightFill: { height: 6, borderRadius: 3 },
  navRow: { flexDirection: "row", alignItems: "center" },
  grid: { flexDirection: "row", flexWrap: "wrap", justifyContent: "space-between", gap: 10 },
  // Two per row: half the width, minus half the 10pt gutter.
  tileWrap: { width: "48%" },
  tile: {
    borderWidth: StyleSheet.hairlineWidth,
    paddingVertical: 14,
    paddingHorizontal: 12,
    gap: 6,
    minHeight: 76,
    justifyContent: "center",
  },
  practice: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    borderWidth: 1,
    borderStyle: "dashed",
    paddingVertical: 12,
    paddingHorizontal: 14,
  },
  disclaimer: { fontSize: 11, lineHeight: 15, textAlign: "center", paddingBottom: 8 },
});
