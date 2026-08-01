# Building the Android APK

> **Why not a prebuilt APK in this repo?** The app uses a **native** module
> (`react-native-plaid-link-sdk`), so it must be compiled — you can't ship a
> universal APK from source alone. Compiling needs the Android SDK + JDK 17,
> which weren't available on the machine that generated this project. The
> fastest path that needs **none** of that locally is Expo's **EAS cloud
> build**, below. A fully local option is also documented.

## Option A — EAS cloud build (recommended, no Android SDK needed)

You need a free Expo account (https://expo.dev). The build runs in Expo's cloud
and gives you a downloadable `.apk`.

```bash
cd mobile
npm install
npm i -g eas-cli

eas login                 # your Expo account
eas init                  # links the project, writes the projectId into app.json
eas build -p android --profile preview
```

- The `preview` profile is configured to output an **APK** (see `eas.json`).
- When the build finishes, the CLI prints a URL — download the `.apk` and
  install it on your phone (enable "Install unknown apps" for your browser/file
  manager the first time).

> Tip: `eas login` is interactive. In this Claude Code session you can run it
> yourself by typing `!eas login` (and the other commands) so they execute in
> your terminal.

## Option B — Fully local build (needs Android toolchain)

Install **Android Studio** (gives you the Android SDK) and **JDK 17**, set
`ANDROID_HOME`, then:

```bash
cd mobile
npm install
npx expo run:android            # builds & installs a dev build on a connected device/emulator
# or a release APK:
npx expo prebuild -p android
cd android && ./gradlew assembleRelease
# APK at: android/app/build/outputs/apk/release/app-release.apk
```

## After installing the APK

The installed app talks to your backend over the network. Two things to set,
**both from the in-app Settings tab** (no rebuild needed):

1. **Server URL** — the phone must reach your backend. Options:
   - Same Wi‑Fi: use your computer's LAN IP, e.g. `http://192.168.1.50:8000/api/v1`
     (run the API with `docker compose up` or `uvicorn ... --host 0.0.0.0`).
   - A tunnel: `ngrok http 8000` / `cloudflared`, then use the public URL + `/api/v1`.
   - A deployed backend URL.
2. **Plaid keys** — paste your Plaid `client_id` / `secret` and pick
   `sandbox` or `production`. They're stored encrypted on the server. Leave
   them empty to use the built-in **mock bank** (demo data, no Plaid account
   needed).

Then: register → **Connect a bank** (Sandbox login `user_good` / `pass_good`) →
Dashboard.

## Notes
- iOS: swap `-p android` for `-p ios` (needs an Apple account for device installs).
- The `production` profile builds an `.aab` (for Play Store); use `preview` for
  a shareable `.apk`.
