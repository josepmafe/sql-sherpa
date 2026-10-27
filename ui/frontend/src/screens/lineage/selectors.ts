// dbt selector syntax for the three modes (UI spec §6.5), and the way back from a selector to a mode.
export type Depth = "all" | 1 | 2 | 3 | 4 | 5;
export type SelectorMode =
  | { mode: "project" }
  | { mode: "path"; name: string; up: Depth; down: Depth }
  | { mode: "domain"; domain: string }
  | { mode: "custom" };

const DEPTHS: readonly Depth[] = [1, 2, 3, 4, 5];
const NAME = String.raw`[\w-]+`;
// `[n]+name+[m]`: both operators present, so a one-sided or atom-only selector is a custom one.
const PATH_SELECTOR = new RegExp(String.raw`^(\d*)\+(${NAME})\+(\d*)$`);
const DOMAIN_SELECTOR = new RegExp(String.raw`^\+config\.meta\.domain:(${NAME})$`);

export function pathSelector(name: string, up: Depth, down: Depth): string {
  return `${up === "all" ? "" : up}+${name}+${down === "all" ? "" : down}`;
}

export function domainSelector(domain: string): string {
  return `+config.meta.domain:${domain}`;
}

export function readMode(select: string): SelectorMode {
  const selector = select.trim();
  if (selector === "") return { mode: "project" };

  const path = PATH_SELECTOR.exec(selector);
  if (path !== null) {
    const up = readDepth(path[1] ?? "");
    const down = readDepth(path[3] ?? "");
    if (up !== undefined && down !== undefined) return { mode: "path", name: path[2] ?? "", up, down };
  }

  const domain = DOMAIN_SELECTOR.exec(selector);
  if (domain !== null) return { mode: "domain", domain: domain[1] ?? "" };

  return { mode: "custom" };
}

// An empty depth is unlimited; a number outside 1-5 is not one of the controls' values.
function readDepth(digits: string): Depth | undefined {
  if (digits === "") return "all";
  return DEPTHS.find((depth) => String(depth) === digits);
}
