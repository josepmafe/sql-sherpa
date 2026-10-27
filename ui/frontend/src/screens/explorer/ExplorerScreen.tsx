import { Alert, Box, Button, Drawer, Group, ScrollArea, Stack, Tabs } from "@mantine/core";
import { useMediaQuery } from "@mantine/hooks";
import { useCallback, useEffect, useRef, useState } from "react";

import { useSearchParams } from "react-router-dom";

import { useCatalog, useHealth } from "../../api/queries";
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
const VISUALLY_HIDDEN_STYLE = {
  position: "absolute",
  width: 1,
  height: 1,
  padding: 0,
  overflow: "hidden",
  clip: "rect(0 0 0 0)",
  whiteSpace: "nowrap",
  border: 0,
} as const;
const SKIP_SHOWN_STYLE = { position: "fixed", top: 8, left: 8, zIndex: 1000 } as const;
const DATABASE_DOWN = "Database unavailable: queries run again once it is back";

export function ExplorerScreen() {
  const [selected, setSelected] = useState<string>();
  const [inspecting, setInspecting] = useState(false);
  const [unknownTable, setUnknownTable] = useState<string>();
  // "Open in Explorer" from Lineage arrives with ?table=<name>; it is read once, when the catalog is there to check it.
  const catalog = useCatalog();
  const [search] = useSearchParams();
  const requested = useRef(search.get("table"));
  const wide = useMediaQuery(WIDE_QUERY, undefined, { getInitialValueInEffect: false });
  // UI spec §3: a database that is down disables only what needs it (Run, Run selection, Preview). Health is polled,
  // so they come back on their own.
  const health = useHealth();
  const blockedReason = health.data?.database === "down" ? DATABASE_DOWN : undefined;
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

  useEffect(() => {
    const name = requested.current;
    if (name === null || !catalog.data) return;
    requested.current = null;
    if (catalog.data.domains.some((domain) => domain.tables.some((table) => table.name === name))) {
      setSelected(name);
      setInspecting(true);
    } else setUnknownTable(name);
  }, [catalog.data]);

  const centre = useRef<HTMLDivElement>(null);
  const skipToEditor = () =>
    centre.current?.querySelector<HTMLElement>(`[data-worksheet="${active.id}"] .cm-content`)?.focus();

  return (
    <Group align="stretch" wrap="nowrap" gap="md" h="calc(100vh - 100px)">
      <SkipToEditor onSkip={skipToEditor} />
      {/* The vertical scrollbar gets its own gutter, so it never covers the right-aligned "⋯" buttons. */}
      <ScrollArea style={TREE_STYLE} offsetScrollbars="y">
        <CatalogTree
          selected={selected}
          onSelect={inspect}
          onPreview={(name) => setState((previous) => openPreview(previous, name))}
          onInsertSelect={(name) => setState((previous) => insertSelect(previous, name))}
          previewBlocked={blockedReason}
        />
      </ScrollArea>
      <Box ref={centre} style={{ flex: 1, minWidth: 0 }}>
        {unknownTable !== undefined && (
          <Alert color="yellow" role="alert" mb="sm" withCloseButton onClose={() => setUnknownTable(undefined)}>
            No mart named {unknownTable}.
          </Alert>
        )}
        {/* Every panel stays mounted (hidden with display: none, not React's Activity, which would run its effects'
            cleanup), so switching tabs never cancels a running query. */}
        <Tabs
          value={active.id}
          onChange={(id) => id !== null && setState((previous) => selectWorksheet(previous, id))}
          keepMountedMode="display-none"
        >
          <Stack gap="sm">
            <Group justify="space-between" wrap="nowrap" align="flex-start">
              <Box style={{ flex: 1, minWidth: 0 }}>
                <WorksheetTabs
                  worksheets={state.worksheets}
                  activeId={active.id}
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
            {state.worksheets.map((worksheet) => (
              <Tabs.Panel key={worksheet.id} value={worksheet.id} data-worksheet={worksheet.id}>
                <WorksheetPanel
                  worksheet={worksheet}
                  activeTable={selected}
                  blockedReason={blockedReason}
                  onSqlChange={changeSql}
                  onSettled={record}
                  onRunStarted={markRunStarted}
                />
              </Tabs.Panel>
            ))}
          </Stack>
        </Tabs>
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

// The Explorer's first Tab stop, visible only while focused, so a keyboard user need not go through the whole tree.
function SkipToEditor({ onSkip }: { onSkip: () => void }) {
  const [focused, setFocused] = useState(false);
  return (
    <Button
      size="xs"
      style={focused ? SKIP_SHOWN_STYLE : VISUALLY_HIDDEN_STYLE}
      onFocus={() => setFocused(true)}
      onBlur={() => setFocused(false)}
      onClick={onSkip}
    >
      Skip to the SQL editor
    </Button>
  );
}
