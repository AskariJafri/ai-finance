import axios, {
  AxiosError,
  AxiosRequestConfig,
  InternalAxiosRequestConfig,
} from "axios";

import { currentBaseUrl } from "../stores/appConfig";
import { useSession } from "../stores/session";

export const api = axios.create({ timeout: 20000 });

// Attach the current access token + the runtime-configured base URL to every
// request (base URL can be changed in-app via Settings).
api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  config.baseURL = currentBaseUrl();
  const token = useSession.getState().accessToken;
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

// Single-flight refresh: if many requests 401 at once, only one refresh runs.
let refreshPromise: Promise<string | null> | null = null;

async function refreshAccessToken(): Promise<string | null> {
  const { refreshToken, setTokens, signOut } = useSession.getState();
  if (!refreshToken) return null;
  try {
    // Bare axios call to avoid recursing through this interceptor.
    const resp = await axios.post(`${currentBaseUrl()}/auth/refresh`, {
      refresh_token: refreshToken,
    });
    await setTokens(resp.data.access_token, resp.data.refresh_token);
    return resp.data.access_token as string;
  } catch {
    await signOut();
    return null;
  }
}

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const original = error.config as (AxiosRequestConfig & { _retry?: boolean }) | undefined;
    const status = error.response?.status;

    if (status === 401 && original && !original._retry) {
      original._retry = true;
      refreshPromise = refreshPromise ?? refreshAccessToken();
      const newToken = await refreshPromise;
      refreshPromise = null;
      if (newToken) {
        original.headers = { ...original.headers, Authorization: `Bearer ${newToken}` };
        return api.request(original);
      }
    }
    return Promise.reject(error);
  },
);
