import { View } from "react-native";
import Svg, { Circle, G } from "react-native-svg";

import { CategorySpend } from "../api/types";
import { useTheme } from "../theme";

const FALLBACK_COLORS = [
  "#4F46E5",
  "#F59E0B",
  "#EF4444",
  "#16A34A",
  "#EC4899",
  "#06B6D4",
  "#8B5CF6",
  "#64748B",
];

/**
 * Lightweight donut chart built on react-native-svg (no heavy native charting
 * dependency). Swap for Victory Native XL later if richer interactions are
 * needed — the data shape stays the same.
 */
export function SpendDonut({
  data,
  size = 180,
  strokeWidth = 26,
}: {
  data: CategorySpend[];
  size?: number;
  strokeWidth?: number;
}) {
  const theme = useTheme();
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const total = data.reduce((sum, d) => sum + d.amount, 0);

  let offset = 0;
  const segments =
    total > 0
      ? data.map((d, i) => {
          const fraction = d.amount / total;
          const dash = fraction * circumference;
          const seg = {
            color: d.color ?? FALLBACK_COLORS[i % FALLBACK_COLORS.length],
            dash,
            gap: circumference - dash,
            offset,
          };
          offset -= dash;
          return seg;
        })
      : [];

  return (
    <View style={{ width: size, height: size }}>
      <Svg width={size} height={size}>
        <G rotation={-90} origin={`${size / 2}, ${size / 2}`}>
          {/* Track */}
          <Circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            stroke={theme.colors.surfaceAlt}
            strokeWidth={strokeWidth}
            fill="none"
          />
          {segments.map((seg, i) => (
            <Circle
              key={i}
              cx={size / 2}
              cy={size / 2}
              r={radius}
              stroke={seg.color}
              strokeWidth={strokeWidth}
              strokeDasharray={`${seg.dash} ${seg.gap}`}
              strokeDashoffset={seg.offset}
              strokeLinecap="butt"
              fill="none"
            />
          ))}
        </G>
      </Svg>
    </View>
  );
}
