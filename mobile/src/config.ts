import Constants from "expo-constants";

/**
 * API base URL resolution order:
 *   1. EXPO_PUBLIC_API_BASE_URL env var (recommended; set in .env)
 *   2. app.json -> expo.extra.apiBaseUrl
 *   3. Android emulator default (10.0.2.2 maps to host localhost)
 *
 * On a physical device, set EXPO_PUBLIC_API_BASE_URL to your machine's LAN IP,
 * e.g. http://192.168.1.50:8000/api/v1
 */
const fromEnv = process.env.EXPO_PUBLIC_API_BASE_URL;
const fromExtra = (Constants.expoConfig?.extra as { apiBaseUrl?: string } | undefined)
  ?.apiBaseUrl;

export const API_BASE_URL = fromEnv ?? fromExtra ?? "http://10.0.2.2:8000/api/v1";
