import type { components } from "../../api/schema";

type Tile = components["schemas"]["Tile"];

const NONE = "—";
const PERCENT = 100;
const CHANGE_DECIMALS = 1;
const PERIOD = "the previous 30 days";

/** A tile's value for display: the API's Decimal string is only parsed here. */
export function formatValue(tile: Tile): string {
  if (tile.value === null) return NONE;
  const value = Number(tile.value);
  switch (tile.format) {
    case "currency":
      return currency(value, tile.decimals);
    case "percent":
      return `${number(value * PERCENT, tile.decimals)}%`;
    case "count":
      return number(value, tile.decimals);
  }
}

export function formatCurrency(amount: string, decimals = 0): string {
  return currency(Number(amount), decimals);
}

/** The change with its arrow: relative as a percentage, points of a percent tile as "pp". */
export function formatChange(tile: Tile): string {
  if (tile.change === null || tile.direction === null) return NONE;
  const amount = changeAmount(tile);
  switch (tile.direction) {
    case "up":
      return `▲ ${amount}`;
    case "down":
      return `▼ ${amount}`;
    case "flat":
      return "0.0%";
  }
}

/** The change in words, for assistive technology and for those who do not tell the colours apart. */
export function describeChange(tile: Tile): string {
  if (tile.change === null || tile.direction === null) return `no comparison with ${PERIOD}`;
  if (tile.direction === "flat") return `unchanged on ${PERIOD}`;
  const verdict = tile.good === null ? "" : tile.good ? ", an improvement" : ", a deterioration";
  return `${tile.direction} ${changeAmount(tile)} on ${PERIOD}${verdict}`;
}

function changeAmount(tile: Tile): string {
  const amount = number(Math.abs(Number(tile.change)) * PERCENT, CHANGE_DECIMALS);
  return tile.change_kind === "points" ? `${amount} pp` : `${amount}%`;
}

function number(value: number, decimals: number): string {
  return new Intl.NumberFormat("en-GB", { minimumFractionDigits: decimals, maximumFractionDigits: decimals }).format(value);
}

function currency(value: number, decimals: number): string {
  return new Intl.NumberFormat("en-GB", {
    style: "currency",
    currency: "EUR",
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(value);
}
