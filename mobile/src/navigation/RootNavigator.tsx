import {
  DarkTheme,
  DefaultTheme,
  NavigationContainer,
} from "@react-navigation/native";
import { createBottomTabNavigator } from "@react-navigation/bottom-tabs";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import { useEffect } from "react";
import { ActivityIndicator, Text, View } from "react-native";

import { ErrorBoundary } from "../components/ErrorBoundary";
import { AuthScreen } from "../screens/AuthScreen";
import { ChatScreen } from "../screens/ChatScreen";
import { DashboardScreen } from "../screens/DashboardScreen";
import { InsightsScreen } from "../screens/InsightsScreen";
import { LinkBankScreen } from "../screens/LinkBankScreen";
import { ManageCategoriesScreen } from "../screens/ManageCategoriesScreen";
import { SettingsScreen } from "../screens/SettingsScreen";
import { TransactionsScreen } from "../screens/TransactionsScreen";
import { AutoTraderScreen } from "../screens/invest/AutoTraderScreen";
import { BacktestScreen } from "../screens/invest/BacktestScreen";
import { CommitteeScreen } from "../screens/invest/CommitteeScreen";
import { DeployCashScreen } from "../screens/invest/DeployCashScreen";
import { HoldingsScreen } from "../screens/invest/HoldingsScreen";
import { InvestHomeScreen } from "../screens/invest/InvestHomeScreen";
import { PaperTradingScreen } from "../screens/invest/PaperTradingScreen";
import { ProjectionsScreen } from "../screens/invest/ProjectionsScreen";
import { RebalanceScreen } from "../screens/invest/RebalanceScreen";
import { RiskScreen } from "../screens/invest/RiskScreen";
import { ScenariosScreen } from "../screens/invest/ScenariosScreen";
import { SymbolChartScreen } from "../screens/invest/SymbolChartScreen";
import { WatchlistScreen } from "../screens/invest/WatchlistScreen";
import { useAppConfig } from "../stores/appConfig";
import { useSession } from "../stores/session";
import { useTheme } from "../theme";

const Tab = createBottomTabNavigator();
const Stack = createNativeStackNavigator();
const InvestNav = createNativeStackNavigator();

function InvestStack() {
  const theme = useTheme();
  return (
    <ErrorBoundary label="Invest">
    <InvestNav.Navigator
      screenOptions={{
        headerStyle: { backgroundColor: theme.colors.background },
        headerTintColor: theme.colors.text,
        headerShadowVisible: false,
      }}
    >
      <InvestNav.Screen
        name="InvestHome"
        component={InvestHomeScreen}
        options={{ headerShown: false }}
      />
      <InvestNav.Screen
        name="Holdings"
        component={HoldingsScreen}
        options={{ title: "" }}
      />
      <InvestNav.Screen
        name="Watchlist"
        component={WatchlistScreen}
        options={{ title: "" }}
      />
      <InvestNav.Screen
        name="SymbolChart"
        component={SymbolChartScreen}
        options={{ title: "" }}
      />
      <InvestNav.Screen
        name="Committee"
        component={CommitteeScreen}
        options={{ title: "" }}
      />
      <InvestNav.Screen
        name="DeployCash"
        component={DeployCashScreen}
        options={{ title: "" }}
      />
      <InvestNav.Screen
        name="PaperTrading"
        component={PaperTradingScreen}
        options={{ title: "" }}
      />
      <InvestNav.Screen
        name="AutoTrader"
        component={AutoTraderScreen}
        options={{ title: "" }}
      />
      <InvestNav.Screen name="Risk" component={RiskScreen} options={{ title: "" }} />
      <InvestNav.Screen
        name="Projections"
        component={ProjectionsScreen}
        options={{ title: "" }}
      />
      <InvestNav.Screen
        name="Scenarios"
        component={ScenariosScreen}
        options={{ title: "" }}
      />
      <InvestNav.Screen name="Backtest" component={BacktestScreen} options={{ title: "" }} />
      <InvestNav.Screen
        name="Rebalance"
        component={RebalanceScreen}
        options={{ title: "" }}
      />
    </InvestNav.Navigator>
    </ErrorBoundary>
  );
}

function Tabs() {
  const theme = useTheme();
  return (
    <Tab.Navigator
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: theme.colors.primary,
        tabBarInactiveTintColor: theme.colors.textMuted,
        tabBarStyle: {
          backgroundColor: theme.colors.surface,
          borderTopColor: theme.colors.border,
        },
      }}
    >
      <Tab.Screen
        name="Dashboard"
        component={DashboardScreen}
        options={{ tabBarIcon: ({ color }) => <Text style={{ color, fontSize: 18 }}>◎</Text> }}
      />
      <Tab.Screen
        name="Transactions"
        component={TransactionsScreen}
        options={{ tabBarIcon: ({ color }) => <Text style={{ color, fontSize: 18 }}>≡</Text> }}
      />
      <Tab.Screen
        name="Insights"
        component={InsightsScreen}
        options={{ tabBarIcon: ({ color }) => <Text style={{ color, fontSize: 18 }}>◐</Text> }}
      />
      <Tab.Screen
        name="Invest"
        component={InvestStack}
        options={{ tabBarIcon: ({ color }) => <Text style={{ color, fontSize: 18 }}>▲</Text> }}
      />
      <Tab.Screen
        name="Ask"
        component={ChatScreen}
        options={{ tabBarIcon: ({ color }) => <Text style={{ color, fontSize: 18 }}>✦</Text> }}
      />
      <Tab.Screen
        name="Settings"
        component={SettingsScreen}
        options={{ tabBarIcon: ({ color }) => <Text style={{ color, fontSize: 18 }}>⚙</Text> }}
      />
    </Tab.Navigator>
  );
}

export function RootNavigator() {
  const theme = useTheme();
  const status = useSession((s) => s.status);
  const hydrate = useSession((s) => s.hydrate);
  const hydrateConfig = useAppConfig((s) => s.hydrate);
  const configHydrated = useAppConfig((s) => s.hydrated);

  useEffect(() => {
    hydrateConfig();
    hydrate();
  }, [hydrate, hydrateConfig]);

  const navTheme = theme.dark ? DarkTheme : DefaultTheme;

  if (status === "loading" || !configHydrated) {
    return (
      <View
        style={{
          flex: 1,
          alignItems: "center",
          justifyContent: "center",
          backgroundColor: theme.colors.background,
        }}
      >
        <ActivityIndicator color={theme.colors.primary} />
      </View>
    );
  }

  return (
    <NavigationContainer theme={navTheme}>
      {status === "authenticated" ? (
        <Stack.Navigator>
          <Stack.Screen name="Tabs" component={Tabs} options={{ headerShown: false }} />
          <Stack.Screen
            name="LinkBank"
            component={LinkBankScreen}
            options={{ presentation: "modal", headerShown: false }}
          />
          <Stack.Screen
            name="ManageCategories"
            component={ManageCategoriesScreen}
            options={{ title: "Categories" }}
          />
        </Stack.Navigator>
      ) : (
        <AuthScreen />
      )}
    </NavigationContainer>
  );
}
