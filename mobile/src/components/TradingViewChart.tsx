import { createElement } from "react";
import { Linking, Platform, View } from "react-native";
import { WebView } from "react-native-webview";

import { useTheme } from "../theme";

/**
 * TradingView's live chart widget, embedded as TradingView publishes it.
 *
 * This is a licensed *display*: TradingView provides the widget free for use
 * in apps and sites, with its attribution kept in place. The app cannot (and
 * must not) read prices out of it — anything the app calculates with comes
 * from the brokerage quote or the market-data provider. Coverage and delay
 * depend on TradingView's own licence per exchange; some TSX data appears
 * delayed in the free widget.
 */
export function TradingViewChart({ symbol, height = 380 }: { symbol: string; height?: number }) {
  const theme = useTheme();
  const html = widgetHtml(toTradingViewSymbol(symbol), theme.dark ? "dark" : "light");

  if (Platform.OS === "web") {
    // react-native-webview has no web build; a sandboxed iframe is the
    // browser equivalent.
    return (
      <View style={{ height, borderRadius: 12, overflow: "hidden" }}>
        {createElement("iframe", {
          srcDoc: html,
          title: `${symbol} live chart`,
          style: { border: 0, width: "100%", height: "100%" },
          sandbox: "allow-scripts allow-same-origin allow-popups",
        })}
      </View>
    );
  }

  return (
    <View style={{ height, borderRadius: 12, overflow: "hidden" }}>
      <WebView
        originWhitelist={["*"]}
        source={{ html, baseUrl: "https://www.tradingview.com" }}
        javaScriptEnabled
        domStorageEnabled
        scrollEnabled={false}
        style={{ backgroundColor: "transparent" }}
        // The attribution link opens in the system browser, not inside the card.
        onShouldStartLoadWithRequest={(req) => {
          const internal =
            req.url.startsWith("about:") ||
            req.url.startsWith("data:") ||
            req.url.includes("tradingview.com/widgetembed") ||
            req.url.includes("s.tradingview.com") ||
            req.url.includes("s3.tradingview.com") ||
            req.url === "https://www.tradingview.com/";
          if (!internal && req.navigationType === "click") {
            Linking.openURL(req.url);
            return false;
          }
          return true;
        }}
      />
    </View>
  );
}

/** App tickers are Yahoo-style (VFV.TO); TradingView wants EXCHANGE:SYMBOL. */
export function toTradingViewSymbol(symbol: string): string {
  const s = symbol.toUpperCase();
  const venues: [string, string][] = [
    [".TO", "TSX"],
    [".V", "TSXV"],
    [".CN", "CSE"],
    [".NE", "NEO"],
  ];
  for (const [suffix, exchange] of venues) {
    if (s.endsWith(suffix)) return `${exchange}:${s.slice(0, -suffix.length).replace(/-/g, ".")}`;
  }
  return s; // US listings resolve without an exchange prefix
}

function widgetHtml(tvSymbol: string, theme: "dark" | "light"): string {
  const config = {
    autosize: true,
    symbol: tvSymbol,
    interval: "D",
    timezone: "America/Toronto",
    theme,
    style: "1",
    locale: "en",
    allow_symbol_change: false,
    hide_side_toolbar: true,
    calendar: false,
    support_host: "https://www.tradingview.com",
  };
  // Markup as published by TradingView's embed builder, including the
  // copyright/attribution block its terms require.
  return `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<style>html,body{margin:0;height:100%;background:transparent}.tradingview-widget-container{height:100%}</style></head>
<body><div class="tradingview-widget-container" style="height:100%;width:100%">
<div class="tradingview-widget-container__widget" style="height:calc(100% - 24px);width:100%"></div>
<div class="tradingview-widget-copyright" style="font:11px sans-serif;text-align:right;padding:4px 6px">
<a href="https://www.tradingview.com/" rel="noopener nofollow" target="_blank" style="color:#2962FF">Track all markets on TradingView</a></div>
<script type="text/javascript" src="https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js" async>
${JSON.stringify(config)}
</script></div></body></html>`;
}
