// Web Fast Refresh and the browser entry shim. A no-op on native, so it sits
// above the rest unconditionally rather than behind a Platform branch.
import "@expo/metro-runtime";
import { registerRootComponent } from "expo";

import App from "./App";

registerRootComponent(App);
