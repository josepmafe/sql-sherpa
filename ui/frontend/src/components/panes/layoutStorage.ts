// Pane sizes and collapsed flags kept in the browser (UI spec §7). One key per pane group, versioned like the others.
export const LAYOUT_KEY_PREFIX = "sherpa.v1.layout.";

export type PaneLimits = Record<string, { min: number; max: number }>;
/** Pixel sizes of the panes that are open and the collapsed flags: nothing else is stored. */
export type StoredLayout = {
  sizes: Record<string, number>;
  collapsed: Record<string, boolean>;
};

// `localStorage` is read inside the try: with site data blocked, reading it throws (SecurityError). A stored size is
// clamped to the pane's current limits (a smaller screen or a changed limit); a pane without limits is dropped.
export function readLayout(id: string, limits: PaneLimits): StoredLayout | undefined {
  const loaded = loadLayout(id);
  return loaded && clampLayout(loaded, limits);
}

/** The stored layout as written, not yet clamped: a group loads it once (one warning) and hands it to its panes. */
export function loadLayout(id: string): StoredLayout | undefined {
  const key = LAYOUT_KEY_PREFIX + id;
  try {
    const raw = localStorage.getItem(key);
    if (raw === null) return undefined;
    const parsed = parseLayout(raw);
    if (parsed) return parsed;
    console.warn(`${key} is not a valid pane layout; using the default sizes`);
  } catch (error) {
    console.warn(`${key} could not be read; using the default sizes`, error);
  }
  return undefined;
}

export function writeLayout(id: string, value: StoredLayout): void {
  const key = LAYOUT_KEY_PREFIX + id;
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch (error) {
    // The quota or blocked site data: the session carries on, it just won't survive a reload.
    console.warn(`could not save ${key}`, error);
  }
}

function parseLayout(raw: string): StoredLayout | undefined {
  try {
    const parsed: unknown = JSON.parse(raw);
    return isStoredLayout(parsed) ? parsed : undefined;
  } catch {
    return undefined; // not JSON
  }
}

function isStoredLayout(value: unknown): value is StoredLayout {
  if (typeof value !== "object" || value === null) return false;
  const { sizes, collapsed } = value as Record<string, unknown>;
  return (
    isRecordOf(sizes, (v) => typeof v === "number" && Number.isFinite(v)) &&
    isRecordOf(collapsed, (v) => typeof v === "boolean")
  );
}

function isRecordOf(value: unknown, test: (item: unknown) => boolean): boolean {
  return typeof value === "object" && value !== null && !Array.isArray(value) && Object.values(value).every(test);
}

export function clampLayout(stored: StoredLayout, limits: PaneLimits): StoredLayout {
  const sizes: Record<string, number> = {};
  for (const [paneId, size] of Object.entries(stored.sizes)) {
    const limit = limits[paneId];
    if (limit) sizes[paneId] = Math.min(limit.max, Math.max(limit.min, size));
  }
  const collapsed: Record<string, boolean> = {};
  for (const [paneId, flag] of Object.entries(stored.collapsed)) {
    if (paneId in limits) collapsed[paneId] = flag;
  }
  return { sizes, collapsed };
}
