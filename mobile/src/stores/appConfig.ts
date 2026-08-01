import { create } from "zustand";

import { API_BASE_URL } from "../config";
import { getItem, setItem } from "../lib/secureStore";

const BASE_URL_KEY = "server_base_url";

type AppConfigState = {
  baseUrl: string;
  hydrated: boolean;
  hydrate: () => Promise<void>;
  setBaseUrl: (url: string) => Promise<void>;
};

/**
 * Client-side server connection setting. Stored on-device so an installed APK
 * can be pointed at any backend (LAN IP, tunnel, deployed URL) without a
 * rebuild. Defaults to the value baked in at build time (see config.ts).
 */
export const useAppConfig = create<AppConfigState>((set) => ({
  baseUrl: API_BASE_URL,
  hydrated: false,

  hydrate: async () => {
    const saved = await getItem(BASE_URL_KEY);
    set({ baseUrl: saved ?? API_BASE_URL, hydrated: true });
  },

  setBaseUrl: async (url: string) => {
    const trimmed = url.trim().replace(/\/+$/, "");
    await setItem(BASE_URL_KEY, trimmed);
    set({ baseUrl: trimmed });
  },
}));

export function currentBaseUrl(): string {
  return useAppConfig.getState().baseUrl;
}
