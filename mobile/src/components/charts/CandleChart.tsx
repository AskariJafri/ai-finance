import { Fragment } from "react";
import { StyleSheet } from "react-native";
import Svg, { Line, Rect } from "react-native-svg";

import { Candle } from "../../api/types";
import { useTheme } from "../../theme";

/**
 * OHLC candlestick chart with a volume strip.
 *
 * Every coordinate is passed through `finite()` before it reaches an SVG prop:
 * a NaN or Infinity in react-native-svg takes the whole app down natively
 * (a process crash, not a catchable JS error), and market feeds legitimately
 * contain gaps. Same rule as LineChart.
 */
export function CandleChart({
  candles,
  width = 320,
  height = 220,
  previousClose,
}: {
  candles: Candle[];
  width?: number;
  height?: number;
  previousClose?: number | null;
}) {
  const theme = useTheme();
  const finite = (v: number, fallback = 0) => (Number.isFinite(v) ? v : fallback);

  // Drop any bar that isn't fully finite rather than trying to patch it — a
  // half-drawn candle is a lie about the price.
  const bars = candles.filter(
    (c) =>
      Number.isFinite(c.open) &&
      Number.isFinite(c.high) &&
      Number.isFinite(c.low) &&
      Number.isFinite(c.close),
  );
  if (bars.length === 0) return null;

  const volumeH = Math.round(height * 0.18);
  const gap = 6;
  const priceH = height - volumeH - gap;
  const pad = 4;
  const plotH = priceH - pad * 2;

  let max = Math.max(...bars.map((c) => c.high));
  let min = Math.min(...bars.map((c) => c.low));
  // A perfectly flat series (halted symbol, single bar) would divide by zero.
  if (!(max > min)) {
    const mid = finite(max, 1) || 1;
    max = mid * 1.001;
    min = mid * 0.999;
  }
  const span = max - min;

  const cellW = width / bars.length;
  // Leave a hairline between candles, but never collapse the body to nothing.
  const bodyW = Math.max(1, Math.min(cellW * 0.66, 14));

  const yOf = (price: number) =>
    finite(pad + plotH * (1 - (finite(price, min) - min) / span), pad);

  const maxVolume = Math.max(...bars.map((c) => finite(c.volume ?? 0)), 1);
  const up = theme.colors.positive;
  const down = theme.colors.negative;

  const prevCloseY =
    previousClose != null &&
    Number.isFinite(previousClose) &&
    previousClose <= max &&
    previousClose >= min
      ? yOf(previousClose)
      : null;

  return (
    <Svg width={width} height={height}>
      {/* Recessive gridlines, matching LineChart's density. */}
      {[0.25, 0.5, 0.75].map((f) => (
        <Line
          key={f}
          x1={0}
          x2={width}
          y1={pad + plotH * f}
          y2={pad + plotH * f}
          stroke={theme.colors.border}
          strokeWidth={StyleSheet.hairlineWidth}
        />
      ))}

      {/* Previous close: the reference every "is it up today?" read is against. */}
      {prevCloseY != null ? (
        <Line
          x1={0}
          x2={width}
          y1={prevCloseY}
          y2={prevCloseY}
          stroke={theme.colors.textMuted}
          strokeWidth={1}
          strokeDasharray="3,3"
        />
      ) : null}

      {bars.map((c, i) => {
        const cx = finite(i * cellW + cellW / 2);
        const color = c.close >= c.open ? up : down;

        const yOpen = yOf(c.open);
        const yClose = yOf(c.close);
        const bodyTop = Math.min(yOpen, yClose);
        // A doji (open === close) still has to be visible.
        const bodyH = Math.max(1, Math.abs(yClose - yOpen));

        const vh = finite((finite(c.volume ?? 0) / maxVolume) * volumeH);

        return (
          <Fragment key={c.time}>
            <Line
              x1={cx}
              x2={cx}
              y1={yOf(c.high)}
              y2={yOf(c.low)}
              stroke={color}
              strokeWidth={1}
            />
            <Rect
              x={finite(cx - bodyW / 2)}
              y={bodyTop}
              width={bodyW}
              height={bodyH}
              fill={color}
            />
            <Rect
              x={finite(cx - bodyW / 2)}
              y={finite(height - vh)}
              width={bodyW}
              height={vh}
              fill={color}
              opacity={0.35}
            />
          </Fragment>
        );
      })}
    </Svg>
  );
}
