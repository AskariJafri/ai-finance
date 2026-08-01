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

import { useInvestSync, usePortfolio } from "../../api/hooks";
import { Button } from "../../components/Button";
import { Card } from "../../components/Card";
import { InvestAiCard } from "../../components/InvestAiCard";
import { useTheme } from "../../theme";
import { formatCurrency, formatPercent } from "../../utils/format";

type NavItem = { screen: string; title: string; subtitle: string };

// AI + trading tools — usable without any real holdings entered.
const AI_CARDS: NavItem[] = [
  { screen: "DeployCash", title: "Invest Cash", subtitle: "Turn an amount into a concrete buy plan" },
  { screen: "Committee", title: "AI Committee", subtitle: "Multi-agent BUY / SKIP / HOLD verdict on a stock" },
  { screen: "PaperTrading", title: "Paper Trading", subtitle: "Practise with $100k of simulated cash" },
  { screen: "AutoTrader", title: "Auto-Trader", subtitle: "Rules-based bot — safe, off by default" },
  { screen: "Watchlist", title: "Watchlist", subtitle: "Track tickers with live prices" },
];

// Analytics over the real holdings you enter.
const PORTFOLIO_CARDS: NavItem[] = [
  { screen: "Holdings", title: "Holdings", subtitle: "What you own — add, edit, remove" },
  { screen: "Risk", title: "Risk", subtitle: "Volatility, VaR, Sharpe, concentration" },
  { screen: "Projections", title: "Projections", subtitle: "Monte Carlo futures with honest bands" },
  { screen: "Scenarios", title: "Stress Test", subtitle: "2008, COVID, rate shocks vs your portfolio" },
  { screen: "Backtest", title: "Backtest & DCA", subtitle: "Test strategies on real history" },
  { screen: "Rebalance", title: "Rebalance", subtitle: "Targets, drift, trade checklist" },
];

export function InvestHomeScreen() {
  const theme = useTheme();
  const navigation = useNavigation<any>();
  const { data, isLoading, refetch, isRefetching } = usePortfolio();
  const sync = useInvestSync();

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
          <Card>
            <Text style={[styles.cardTitle, { color: theme.colors.text }]}>
              Add your holdings to begin
            </Text>
            <Text style={[styles.body, { color: theme.colors.textMuted }]}>
              Tell the app what you own at your broker — Wealthsimple,
              Questrade, TD, wherever — and it does the rest: risk analysis,
              stress tests against 2008 and COVID, Monte Carlo projections, and
              rebalancing suggestions.
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
          </Card>

          <NavSection title="AI & Trading" items={AI_CARDS} onNavigate={(s) => navigation.navigate(s)} />
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
            {data.stale_symbols.length > 0 ? (
              <Text style={{ color: theme.colors.danger, fontSize: 12, marginTop: 8 }}>
                No price data for {data.stale_symbols.join(", ")} — tap Sync data.
              </Text>
            ) : null}
          </Card>

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
                  <Text
                    style={{
                      color: p.unrealized_pl >= 0 ? theme.colors.positive : theme.colors.negative,
                      fontSize: 12,
                    }}
                  >
                    {p.unrealized_pl >= 0 ? "+" : ""}
                    {formatCurrency(p.unrealized_pl)} ({formatPercent(p.unrealized_pl_pct)})
                  </Text>
                </View>
              </View>
            ))}
          </Card>

          <NavSection title="AI & Trading" items={AI_CARDS} onNavigate={(s) => navigation.navigate(s)} />
          <NavSection title="Portfolio analysis" items={PORTFOLIO_CARDS} onNavigate={(s) => navigation.navigate(s)} />

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

function NavSection({
  title,
  items,
  onNavigate,
}: {
  title: string;
  items: NavItem[];
  onNavigate: (screen: string) => void;
}) {
  const theme = useTheme();
  return (
    <View style={{ gap: 10 }}>
      <Text style={[styles.sectionTitle, { color: theme.colors.textMuted }]}>{title}</Text>
      {items.map((c) => (
        <Pressable key={c.screen} onPress={() => onNavigate(c.screen)}>
          <Card>
            <View style={styles.navRow}>
              <View style={{ flex: 1 }}>
                <Text style={[styles.cardTitle, { color: theme.colors.text }]}>{c.title}</Text>
                <Text style={{ color: theme.colors.textMuted, fontSize: 13, marginTop: 2 }}>
                  {c.subtitle}
                </Text>
              </View>
              <Text style={{ color: theme.colors.textMuted, fontSize: 20 }}>›</Text>
            </View>
          </Card>
        </Pressable>
      ))}
    </View>
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
  positionRow: { flexDirection: "row", alignItems: "center", gap: 12, marginTop: 12 },
  symbol: { fontSize: 15, fontWeight: "700" },
  weightTrack: { height: 6, borderRadius: 3, marginTop: 6, overflow: "hidden" },
  weightFill: { height: 6, borderRadius: 3 },
  navRow: { flexDirection: "row", alignItems: "center" },
  disclaimer: { fontSize: 11, lineHeight: 15, textAlign: "center", paddingBottom: 8 },
});
