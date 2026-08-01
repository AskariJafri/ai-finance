import { create } from "zustand";

import { deleteItem, getItem, setItem } from "../lib/secureStore";

const ACCESS_KEY = "access_token";
const REFRESH_KEY = "refresh_token";

export type SessionStatus = "loading" | "authenticated" | "unauthenticated";

type SessionState = {
  status: SessionStatus;
  accessToken: string | null;
  refreshToken: string | null;
  hydrate: () => Promise<void>;
  setTokens: (accessToken: string, refreshToken: string) => Promise<void>;
  signOut: () => Promise<void>;
};

export const useSession = create<SessionState>((set) => ({
  status: "loading",
  accessToken: null,
  refreshToken: null,

  hydrate: async () => {
    const [accessToken, refreshToken] = await Promise.all([
      getItem(ACCESS_KEY),
      getItem(REFRESH_KEY),
    ]);
    set({
      accessToken,
      refreshToken,
      status: accessToken ? "authenticated" : "unauthenticated",
    });
  },

  setTokens: async (accessToken, refreshToken) => {
    await Promise.all([
      setItem(ACCESS_KEY, accessToken),
      setItem(REFRESH_KEY, refreshToken),
    ]);
    set({ accessToken, refreshToken, status: "authenticated" });
  },

  signOut: async () => {
    await Promise.all([deleteItem(ACCESS_KEY), deleteItem(REFRESH_KEY)]);
    set({ accessToken: null, refreshToken: null, status: "unauthenticated" });
  },
}));
