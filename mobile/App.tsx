import { StatusBar } from "expo-status-bar";
import { useColorScheme } from "react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { ErrorBoundary } from "./src/components/ErrorBoundary";
import { RootNavigator } from "./src/navigation/RootNavigator";
import { QueryProvider } from "./src/providers/QueryProvider";

export default function App() {
  const scheme = useColorScheme();
  return (
    <SafeAreaProvider>
      <QueryProvider>
        <StatusBar style={scheme === "dark" ? "light" : "dark"} />
        {/* App-wide backstop: turns any JS crash into a visible, retryable
            screen instead of a silent close. (Native crashes bypass this.) */}
        <ErrorBoundary label="App">
          <RootNavigator />
        </ErrorBoundary>
      </QueryProvider>
    </SafeAreaProvider>
  );
}
