import { Box, Drawer, Group, ScrollArea, Stack } from "@mantine/core";
import { useMediaQuery } from "@mantine/hooks";
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

const TREE_STYLE = { flexShrink: 0, width: 280 } as const;
const INSPECTOR_STYLE = { flexShrink: 0, width: 300 } as const;
// From 1280 px the Inspector has its own column. Below, it opens as a drawer, so the editor keeps about 550 px at
// 1024 px (the nav rail, the tree and the gaps take the rest).
const WIDE_QUERY = "(min-width: 80em)";
const INSPECTOR_TITLE = "Table details";

export function ExplorerScreen() {
  const [selected, setSelected] = useState<string>();
  const [inspecting, setInspecting] = useState(false);
  const wide = useMediaQuery(WIDE_QUERY, undefined, { getInitialValueInEffect: false });
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
  const inspect = (table: string) => {
    setSelected(table);
    setInspecting(true);
  };

  return (
    <Group align="stretch" wrap="nowrap" gap="md" h="calc(100vh - 100px)">
      {/* The vertical scrollbar gets its own gutter, so it never covers the right-aligned "⋯" buttons. */}
      <ScrollArea style={TREE_STYLE} offsetScrollbars="y">
        <CatalogTree
          selected={selected}
          onSelect={inspect}
          onPreview={(name) => setState((previous) => openPreview(previous, name))}
          onInsertSelect={(name) => setState((previous) => insertSelect(previous, name))}
        />
      </ScrollArea>
      <Box style={{ flex: 1, minWidth: 0 }}>
        <Stack gap="sm">
          <Group justify="space-between" wrap="nowrap" align="flex-start">
            <Box style={{ flex: 1, minWidth: 0 }}>
              <WorksheetTabs
                worksheets={state.worksheets}
                activeId={active.id}
                onSelect={(id) => setState((previous) => selectWorksheet(previous, id))}
                onAdd={() => setState(addWorksheet)}
                onClose={(id) => setState((previous) => closeWorksheet(previous, id))}
                onRename={(id, name) => setState((previous) => updateWorksheet(previous, id, { name }))}
              />
            </Box>
            <Box style={{ flexShrink: 0 }}>
              <QueryHistory
                history={state.history}
                onPick={(sql) => setState((previous) => updateWorksheet(previous, active.id, { sql }))}
              />
            </Box>
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
      {wide ? (
        <Box component="aside" aria-label={INSPECTOR_TITLE} style={INSPECTOR_STYLE}>
          <ScrollArea h="100%" offsetScrollbars="y">
            <Inspector table={selected} />
          </ScrollArea>
        </Box>
      ) : (
        <Drawer
          opened={inspecting}
          onClose={() => setInspecting(false)}
          position="right"
          size="sm"
          title={INSPECTOR_TITLE}
        >
          <Inspector table={selected} />
        </Drawer>
      )}
    </Group>
  );
}
