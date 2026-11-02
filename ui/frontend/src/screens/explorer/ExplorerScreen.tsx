import { Alert, Box, Button, Flex, Group, Tabs } from "@mantine/core";
import { useMediaQuery } from "@mantine/hooks";
import { useCallback, useEffect, useRef, useState } from "react";

import { useSearchParams } from "react-router-dom";

import { useCatalog, useHealth } from "../../api/queries";
import { Pane, PaneGroup, PaneHandle } from "../../components/panes";
import { INSPECTOR_PANE, InspectorOverlay, InspectorPane } from "./InspectorPane";
import { QueryHistory } from "./QueryHistory";
import { TREE_PANE, TREE_STRIP_WIDTH, TreePane } from "./TreePane";
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

// Pane sizes in pixels (UI spec §6.1, §6.4). The centre takes what the others leave.
const LAYOUT_ID = "explorer";
const CENTRE_PANE = "worksheets";
const TREE_DEFAULT = 280;
const TREE_MIN = 200;
const TREE_MAX = 480;
const CENTRE_MIN = 360;
const CENTRE_MAX = 4000;
const INSPECTOR_DEFAULT = 300;
const INSPECTOR_MIN = 240;
const INSPECTOR_MAX = 520;
// From 1280 px the Inspector is a pane. Below, it opens over the editor, so the editor keeps a usable width at 1024 px.
const WIDE_QUERY = "(min-width: 80em)";
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
const POPUP_SELECTOR = '[role="menu"], [role="listbox"], .cm-tooltip';
const DATABASE_DOWN = "Database unavailable: queries run again once it is back";

export function ExplorerScreen() {
  const [selected, setSelected] = useState<string>();
  const [inspecting, setInspecting] = useState(false);
  const [inspectCount, setInspectCount] = useState(0);
  const inspectorHeading = useRef<HTMLHeadingElement>(null);
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
    setInspectCount((previous) => previous + 1);
  };
  const overlayOpen = !wide && inspecting;
  // Closing the overlay puts the focus back on the row of the table that opened it.
  const closeInspector = useCallback(() => {
    setInspecting(false);
    if (selected !== undefined) {
      document.querySelector<HTMLElement>(`[data-table="${CSS.escape(selected)}"]`)?.focus();
    }
  }, [selected]);
  useEffect(() => {
    if (overlayOpen) inspectorHeading.current?.focus();
  }, [overlayOpen, selected]);
  useEffect(() => {
    if (!overlayOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      // Escape that closes a completion list or a menu is theirs, not the overlay's.
      const inPopup = event.target instanceof Element && event.target.closest(POPUP_SELECTOR) !== null;
      if (event.key === "Escape" && !event.defaultPrevented && !inPopup) closeInspector();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [overlayOpen, closeInspector]);

  useEffect(() => {
    const name = requested.current;
    if (name === null || !catalog.data) return;
    requested.current = null;
    if (catalog.data.domains.some((domain) => domain.tables.some((table) => table.name === name))) {
      setSelected(name);
      setInspecting(true);
      setInspectCount((previous) => previous + 1);
    } else setUnknownTable(name);
  }, [catalog.data]);

  const centre = useRef<HTMLDivElement>(null);
  const skipToEditor = () =>
    centre.current?.querySelector<HTMLElement>(`[data-worksheet="${active.id}"] .cm-content`)?.focus();

  return (
    <Box pos="relative" h="calc(100vh - 100px)">
      <SkipToEditor onSkip={skipToEditor} />
      <PaneGroup id={LAYOUT_ID} direction="horizontal">
        <Pane
          id={TREE_PANE}
          defaultSize={TREE_DEFAULT}
          minSize={TREE_MIN}
          maxSize={TREE_MAX}
          collapsedSize={TREE_STRIP_WIDTH}
        >
          <TreePane
            selected={selected}
            onSelect={inspect}
            onPreview={(name) => setState((previous) => openPreview(previous, name))}
            onInsertSelect={(name) => setState((previous) => insertSelect(previous, name))}
            previewBlocked={blockedReason}
          />
        </Pane>
        <PaneHandle label="Resize the tree" />
        <Pane id={CENTRE_PANE} minSize={CENTRE_MIN} maxSize={CENTRE_MAX}>
          <Flex ref={centre} direction="column" h="100%" gap="sm" pl="xs" pr="xs">
            {unknownTable !== undefined && (
              <Alert color="yellow" role="alert" withCloseButton onClose={() => setUnknownTable(undefined)}>
                No mart named {unknownTable}.
              </Alert>
            )}
            {/* Every panel stays mounted (hidden with display: none, not React's Activity, which would run its effects'
                cleanup), so switching tabs never cancels a running query. */}
            <Tabs
              value={active.id}
              onChange={(id) => id !== null && setState((previous) => selectWorksheet(previous, id))}
              keepMountedMode="display-none"
              style={{
                flex: 1,
                minHeight: 0,
                display: "flex",
                flexDirection: "column",
              }}
            >
              <Group justify="space-between" wrap="nowrap" align="flex-start" mb="sm">
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
                <Tabs.Panel
                  key={worksheet.id}
                  value={worksheet.id}
                  data-worksheet={worksheet.id}
                  style={{ flex: 1, minHeight: 0 }}
                >
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
            </Tabs>
          </Flex>
        </Pane>
        {wide && (
          <>
            <PaneHandle label="Resize the Inspector" />
            <Pane
              id={INSPECTOR_PANE}
              defaultSize={INSPECTOR_DEFAULT}
              minSize={INSPECTOR_MIN}
              maxSize={INSPECTOR_MAX}
              collapsedSize={TREE_STRIP_WIDTH}
            >
              <InspectorPane table={selected} inspectCount={inspectCount} />
            </Pane>
          </>
        )}
      </PaneGroup>
      {overlayOpen && <InspectorOverlay table={selected} onClose={closeInspector} headingRef={inspectorHeading} />}
    </Box>
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
