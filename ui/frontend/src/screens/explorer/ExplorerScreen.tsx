import { Box, Group, ScrollArea, Stack } from "@mantine/core";
import { useCallback, useEffect, useState } from "react";

import { CatalogTree } from "./CatalogTree";
import { Inspector } from "./Inspector";
import { QueryHistory } from "./QueryHistory";
import { type RunOutcome } from "./useWorksheetRun";
import { WorksheetPanel } from "./WorksheetPanel";
import { WorksheetTabs } from "./WorksheetTabs";
import {
  addWorksheet,
  appendHistory,
  closeWorksheet,
  insertSelect,
  loadExplorerState,
  mergeExplorerState,
  openPreview,
  parseExplorerState,
  saveExplorerState,
  selectWorksheet,
  STORAGE_KEY,
  updateWorksheet,
} from "./worksheetStore";

const SIDE_WIDTH = 300;
const SIDE_STYLE = { flexShrink: 0, width: SIDE_WIDTH } as const;

export function ExplorerScreen() {
  const [selected, setSelected] = useState<string>();
  const [state, setState] = useState(() => loadExplorerState());
  useEffect(() => saveExplorerState(state), [state]);
  // Another tab of the Explorer saved: take in its new worksheets and runs instead of overwriting them.
  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key !== STORAGE_KEY || event.newValue === null) return;
      const other = parseExplorerState(event.newValue);
      if (other) setState((previous) => mergeExplorerState(previous, other));
      else console.warn(`another tab saved an invalid ${STORAGE_KEY}; ignoring it`);
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const active = state.worksheets.find((w) => w.id === state.activeId) ?? state.worksheets[0]!;
  // Stable across renders, so the memoized panels skip a render that changes none of their props.
  const record = useCallback(
    (outcome: RunOutcome) =>
      setState((previous) => appendHistory(previous, { ...outcome, ranAt: new Date().toISOString() })),
    [],
  );
  const changeSql = useCallback(
    (id: string, sql: string) => setState((previous) => updateWorksheet(previous, id, { sql })),
    [],
  );
  const markRunStarted = useCallback(
    (id: string) => setState((previous) => updateWorksheet(previous, id, { runOnOpen: false })),
    [],
  );

  return (
    <Group align="stretch" wrap="nowrap" gap="md" h="calc(100vh - 100px)">
      {/* The vertical scrollbar gets its own gutter, so it never covers the right-aligned "⋯" buttons. */}
      <ScrollArea style={SIDE_STYLE} offsetScrollbars="y">
        <CatalogTree
          selected={selected}
          onSelect={setSelected}
          onPreview={(name) => setState((previous) => openPreview(previous, name))}
          onInsertSelect={(name) => setState((previous) => insertSelect(previous, name))}
        />
      </ScrollArea>
      <Box style={{ flex: 1, minWidth: 0 }}>
        <Stack gap="sm">
          <Group justify="space-between" wrap="nowrap">
            <WorksheetTabs
              worksheets={state.worksheets}
              activeId={active.id}
              onSelect={(id) => setState((previous) => selectWorksheet(previous, id))}
              onAdd={() => setState(addWorksheet)}
              onClose={(id) => setState((previous) => closeWorksheet(previous, id))}
              onRename={(id, name) => setState((previous) => updateWorksheet(previous, id, { name }))}
            />
            <QueryHistory
              history={state.history}
              onPick={(sql) => setState((previous) => updateWorksheet(previous, active.id, { sql }))}
            />
          </Group>
          {/* Every worksheet stays mounted, so switching tabs never cancels a running query. */}
          {state.worksheets.map((worksheet) => (
            <Box key={worksheet.id} display={worksheet.id === active.id ? "block" : "none"}>
              <WorksheetPanel
                worksheet={worksheet}
                activeTable={selected}
                onSqlChange={changeSql}
                onSettled={record}
                onRunStarted={markRunStarted}
              />
            </Box>
          ))}
        </Stack>
      </Box>
      <ScrollArea style={SIDE_STYLE} offsetScrollbars="y">
        <Inspector table={selected} />
      </ScrollArea>
    </Group>
  );
}
