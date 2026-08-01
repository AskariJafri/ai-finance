import { useColorScheme } from "react-native";

export type Theme = {
  dark: boolean;
  colors: {
    background: string;
    surface: string;
    surfaceAlt: string;
    border: string;
    text: string;
    textMuted: string;
    primary: string;
    primaryText: string;
    positive: string;
    negative: string;
    danger: string;
  };
  spacing: (n: number) => number;
  radius: { sm: number; md: number; lg: number; xl: number };
};

const base = {
  spacing: (n: number) => n * 8,
  radius: { sm: 8, md: 12, lg: 16, xl: 24 },
};

const light: Theme = {
  dark: false,
  colors: {
    background: "#F5F6F8",
    surface: "#FFFFFF",
    surfaceAlt: "#EEF1F5",
    border: "#E2E6EC",
    text: "#0B0F14",
    textMuted: "#5B6572",
    primary: "#4F46E5",
    primaryText: "#FFFFFF",
    positive: "#16A34A",
    negative: "#DC2626",
    danger: "#DC2626",
  },
  ...base,
};

const dark: Theme = {
  dark: true,
  colors: {
    background: "#0B0F14",
    surface: "#151B23",
    surfaceAlt: "#1D2530",
    border: "#263040",
    text: "#F3F5F7",
    textMuted: "#93A0B0",
    primary: "#7C82FF",
    primaryText: "#0B0F14",
    positive: "#34D399",
    negative: "#F87171",
    danger: "#F87171",
  },
  ...base,
};

export function useTheme(): Theme {
  const scheme = useColorScheme();
  return scheme === "dark" ? dark : light;
}
