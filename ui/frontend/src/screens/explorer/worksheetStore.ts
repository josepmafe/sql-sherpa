// Explorer state kept in the browser (UI spec §7). The key is versioned so a schema change can reset it.
export const STORAGE_KEY = "sherpa.v1.explorer";
export const HISTORY_LIMIT = 200;

export type Worksheet = { id: string; name: string; sql: string };
export type HistoryStatus = "done" | "error" | "cancelled";
export type HistoryEntry = {
  sql: string;
  ranAt: string;
  returnedRows: number | null;
  status: HistoryStatus;
};
export type ExplorerState = {
  worksheets: Worksheet[];
  activeId: string;
  history: HistoryEntry[];
};

const HISTORY_STATUSES: readonly string[] = ["done", "error", "cancelled"];

export function loadExplorerState(storage: Storage = localStorage): ExplorerState {
  try {
    const raw = storage.getItem(STORAGE_KEY);
    if (raw === null) return freshState();
    const parsed: unknown = JSON.parse(raw);
    if (isExplorerState(parsed)) return parsed;
    console.warn(`${STORAGE_KEY} has an unexpected shape; starting with an empty worksheet`);
  } catch (error) {
    console.warn(`${STORAGE_KEY} could not be read; starting with an empty worksheet`, error);
  }
  return freshState();
}

export function saveExplorerState(state: ExplorerState, storage: Storage = localStorage): void {
  try {
    storage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch (error) {
    // Typically the quota: the session carries on, it just won't survive a reload.
    console.warn(`could not save ${STORAGE_KEY}`, error);
  }
}

export function freshState(): ExplorerState {
  const worksheet = newWorksheet([]);
  return { worksheets: [worksheet], activeId: worksheet.id, history: [] };
}

export function addWorksheet(state: ExplorerState): ExplorerState {
  const worksheet = newWorksheet(state.worksheets);
  return {
    ...state,
    worksheets: [...state.worksheets, worksheet],
    activeId: worksheet.id,
  };
}

// The last worksheet stays: there is always one to type into.
export function closeWorksheet(state: ExplorerState, id: string): ExplorerState {
  if (state.worksheets.length <= 1) return state;
  const index = state.worksheets.findIndex((w) => w.id === id);
  if (index < 0) return state;
  const worksheets = state.worksheets.filter((w) => w.id !== id);
  const activeId =
    state.activeId === id ? (worksheets[Math.min(index, worksheets.length - 1)]?.id ?? state.activeId) : state.activeId;
  return { ...state, worksheets, activeId };
}

export function updateWorksheet(
  state: ExplorerState,
  id: string,
  change: Partial<Omit<Worksheet, "id">>,
): ExplorerState {
  return {
    ...state,
    worksheets: state.worksheets.map((w) => (w.id === id ? { ...w, ...change } : w)),
  };
}

export function selectWorksheet(state: ExplorerState, id: string): ExplorerState {
  return state.worksheets.some((w) => w.id === id) ? { ...state, activeId: id } : state;
}

export function appendHistory(state: ExplorerState, entry: HistoryEntry): ExplorerState {
  return { ...state, history: [...state.history, entry].slice(-HISTORY_LIMIT) };
}

// "Insert SELECT" adds a statement after whatever is already typed.
export function insertSelect(state: ExplorerState, qualifiedName: string): ExplorerState {
  const active = state.worksheets.find((w) => w.id === state.activeId);
  if (!active) return state;
  const statement = `select *\nfrom ${qualifiedName}\nlimit 100`;
  const sql = active.sql.trim() === "" ? statement : `${active.sql.trimEnd()}\n\n${statement}`;
  return updateWorksheet(state, active.id, { sql });
}

function newWorksheet(existing: readonly Worksheet[]): Worksheet {
  const names = new Set(existing.map((w) => w.name));
  let n = 1;
  while (names.has(`Worksheet ${n}`)) n += 1;
  return { id: crypto.randomUUID(), name: `Worksheet ${n}`, sql: "" };
}

function isExplorerState(value: unknown): value is ExplorerState {
  if (!isRecord(value)) return false;
  const { worksheets, activeId, history } = value;
  return (
    Array.isArray(worksheets) &&
    worksheets.length > 0 &&
    worksheets.every(isWorksheet) &&
    typeof activeId === "string" &&
    worksheets.some((w) => w.id === activeId) &&
    Array.isArray(history) &&
    history.every(isHistoryEntry)
  );
}

function isWorksheet(value: unknown): value is Worksheet {
  return (
    isRecord(value) && typeof value.id === "string" && typeof value.name === "string" && typeof value.sql === "string"
  );
}

function isHistoryEntry(value: unknown): value is HistoryEntry {
  return (
    isRecord(value) &&
    typeof value.sql === "string" &&
    typeof value.ranAt === "string" &&
    (value.returnedRows === null || typeof value.returnedRows === "number") &&
    typeof value.status === "string" &&
    HISTORY_STATUSES.includes(value.status)
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
