# AI Finance — Mobile (Expo + TypeScript)

React Native app: Auth → Link Bank (Plaid) → Dashboard → Transactions, with
React Query for server state, Zustand for the session, and secure token storage.

## Important: Plaid needs a dev build (not Expo Go)

`react-native-plaid-link-sdk` is a **native module**, so the app must run in an
**Expo dev build**, not Expo Go. Everything except the Plaid Link handoff will
also work in Expo Go, but linking a bank requires the dev build.

## Setup

```bash
cd mobile
npm install
npx expo install --fix        # aligns native package versions with the Expo SDK
cp .env.example .env          # set EXPO_PUBLIC_API_BASE_URL for your device (see file)
```

### Point the app at your backend
- **Android emulator:** `http://10.0.2.2:8000/api/v1` (default)
- **iOS simulator:** `http://localhost:8000/api/v1`
- **Physical device:** `http://<your-computer-LAN-IP>:8000/api/v1`

### Build & run the dev client

```bash
# Local native build (needs Android Studio / Xcode):
npx expo run:android        # or: npx expo run:ios

# …or a cloud dev build via EAS:
npm i -g eas-cli
eas build --profile development --platform android
# install the artifact, then:
npx expo start --dev-client
```

## Flow
1. **Auth** — register or log in (JWT stored in `expo-secure-store`; auto-refresh
   on 401).
2. **Connect a bank** — Dashboard → "Connect a bank" launches Plaid Link. In
   Sandbox use `user_good` / `pass_good`. On success the app exchanges the
   public token; the backend stores an encrypted access token and syncs.
3. **Dashboard** — balance, income/spending/savings, spend-by-category donut,
   recent transactions.
4. **Transactions** — tap a row to recategorize; your correction trains future
   auto-categorization (the backend records a high-priority rule).

5. **Settings** — set the **server URL** (so an installed APK can reach your
   backend) and **add your own Plaid keys** (client_id/secret, sandbox/production).
   Keys are stored encrypted on the server; leave empty to use the mock bank.

## Structure
```
src/
├── api/          axios client (auth + refresh, runtime base URL), React Query hooks, types
├── stores/       Zustand session store + app-config store (server URL), secure storage
├── navigation/   root stack + bottom tabs (Dashboard/Transactions/Settings); auth-gated
├── screens/      Auth, LinkBank, Dashboard, Transactions, Settings
├── components/   Card, Button, SpendDonut (react-native-svg), TransactionRow
├── theme/        design tokens + light/dark
└── utils/        currency/date formatting
```

## Build an installable APK
See [`../docs/BUILD_APK.md`](../docs/BUILD_APK.md). Quickest path (no local
Android SDK): `eas login && eas init && eas build -p android --profile preview`.

## Fallback without a dev build (optional)
If you can't produce a dev build yet, swap `LinkBankScreen` to open Plaid
**Hosted Link** in `expo-web-browser` using a `hosted_link_url` (requires
enabling Hosted Link in the Plaid dashboard and returning the URL from the
backend). The native SDK path above is the default.

## Typecheck
```bash
npm run typecheck
```
