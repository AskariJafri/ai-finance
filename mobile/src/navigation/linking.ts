import { LinkingOptions } from "@react-navigation/native";
import * as Linking from "expo-linking";

/**
 * URL <-> screen mapping.
 *
 * On a phone this is only deep linking, which the app never needed. In a
 * browser it is the difference between a site and a single page that lies
 * about where you are: without it every screen renders at `/`, so reload lands
 * on the Dashboard, the back button leaves the app, and nothing can be
 * bookmarked or shared.
 *
 * The nesting mirrors RootNavigator exactly — stack, then tabs, then the
 * Invest stack — because React Navigation resolves a path by walking that same
 * shape. Dashboard and InvestHome take the empty path, making them the index
 * of their level.
 */
export const linking: LinkingOptions<any> = {
  prefixes: [Linking.createURL("/")],
  config: {
    screens: {
      Tabs: {
        screens: {
          Dashboard: "",
          Transactions: "transactions",
          Insights: "insights",
          Ask: "ask",
          Invest: {
            path: "invest",
            screens: {
              InvestHome: "",
              Holdings: "holdings",
              Watchlist: "watchlist",
              SymbolChart: "symbol/:symbol",
              Committee: "committee",
              Actions: "actions",
              DeployCash: "deploy-cash",
              AutoTrader: "autotrader",
              Risk: "risk",
              Projections: "projections",
              Scenarios: "scenarios",
              Backtest: "backtest",
              Rebalance: "rebalance",
              Paper: "practice",
            },
          },
        },
      },
      Settings: "settings",
      LinkBank: "link-bank",
      ManageCategories: "categories",
    },
  },
};
