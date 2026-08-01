/**
 * Small storage shim over expo-secure-store.
 *
 * On phones this is the platform keychain/keystore. On web (Expo running in a
 * browser) SecureStore has no implementation and throws, so we fall back to
 * localStorage — good enough to hold a JWT and the server URL for a local dev
 * session. Same async surface on both, so callers don't branch.
 */
import { Platform } from "react-native";
import * as SecureStore from "expo-secure-store";

const web = Platform.OS === "web";

export async function getItem(key: string): Promise<string | null> {
  if (web) {
    try {
      return window.localStorage.getItem(key);
    } catch {
      return null;
    }
  }
  return SecureStore.getItemAsync(key);
}

export async function setItem(key: string, value: string): Promise<void> {
  if (web) {
    window.localStorage.setItem(key, value);
    return;
  }
  await SecureStore.setItemAsync(key, value);
}

export async function deleteItem(key: string): Promise<void> {
  if (web) {
    window.localStorage.removeItem(key);
    return;
  }
  await SecureStore.deleteItemAsync(key);
}
