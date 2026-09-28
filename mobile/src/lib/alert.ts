/**
 * Small dialog shim over React Native's Alert.
 *
 * On phones this is the native alert. On web it is *not*: react-native-web
 * ships `class Alert { static alert() {} }` — an export that exists, accepts
 * the call, and does nothing. Nothing crashes and nothing warns, so the
 * failure is silent, and it is worst exactly where it matters. Several screens
 * put the only path to an action inside a button's `onPress` (remove a
 * holding, delete a category, unlink a brokerage); with a no-op Alert those
 * features are simply unreachable in a browser.
 *
 * So web maps onto the dialogs a browser does have:
 *   - nothing actionable        -> window.alert
 *   - one action (+ a cancel)   -> window.confirm
 *   - several actions           -> confirm each in turn, first accepted wins
 *
 * These are blocking and they look like a browser, not like the app. That is
 * the trade being made: a plain dialog that works beats a styled one that
 * doesn't. Replacing this with an in-app modal provider is the upgrade path,
 * and callers would not have to change — the surface is Alert's.
 */
import { Alert, AlertButton, AlertOptions, Platform } from "react-native";

const web = Platform.OS === "web";

function isCancel(button: AlertButton): boolean {
  return button.style === "cancel";
}

/** Everything the browser has to render, as one string. */
function joinText(title: string, message?: string): string {
  return message ? `${title}\n\n${message}` : title;
}

function webAlert(title: string, message?: string, buttons?: AlertButton[]): void {
  const text = joinText(title, message);
  const actions = (buttons ?? []).filter((b) => !isCancel(b));
  const cancel = (buttons ?? []).find(isCancel);

  // No choice to make: tell them, then honour the lone acknowledgement button
  // if one was given (some callers navigate from it).
  if (actions.length === 0) {
    window.alert(text);
    cancel?.onPress?.();
    return;
  }

  const only = actions[0];
  if (actions.length === 1 && only) {
    if (window.confirm(text)) only.onPress?.();
    else cancel?.onPress?.();
    return;
  }

  // An action sheet. confirm() only answers yes/no, so offer each action in
  // the order the caller listed them — that order is already the caller's own
  // statement of which is the ordinary choice and which is the drastic one.
  for (const action of actions) {
    if (window.confirm(`${text}\n\n${action.text ?? "OK"}?`)) {
      action.onPress?.();
      return;
    }
  }
  cancel?.onPress?.();
}

/** Drop-in for `Alert.alert` that also works in a browser. */
export function alert(
  title: string,
  message?: string,
  buttons?: AlertButton[],
  options?: AlertOptions,
): void {
  if (web) {
    webAlert(title, message, buttons);
    return;
  }
  Alert.alert(title, message, buttons, options);
}
