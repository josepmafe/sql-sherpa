import { Alert, Box, Stack, Text } from "@mantine/core";
import { memo, useCallback, useEffect, useMemo, useState } from "react";

import { useCatalog } from "../../api/queries";
import { ResultGrid } from "../../components/ResultGrid";
import {
  EMPTY_VIEW,
  isServerSide,
  maskedColumnNames,
  type ResultView,
  toServerOptions,
} from "../../components/resultView";
import { errorHighlight, SqlEditor } from "../../components/SqlEditor";
import { useWorksheetRun, type RunOutcome } from "./useWorksheetRun";
import type { Worksheet } from "./worksheetStore";

// The callbacks take the worksheet's id, so the screen passes the same functions to every panel and a keystroke in
// one worksheet re-renders no other (each panel holds an editor and a result grid).
type PanelProps = {
  worksheet: Worksheet;
  activeTable?: string;
  onSqlChange: (worksheetId: string, sql: string) => void;
  onSettled: (outcome: RunOutcome) => void;
  onRunStarted: (worksheetId: string) => void;
};

// What was sent and where it started in the editor: an error position counts from the start of the sent text.
type Origin = { sql: string; offset: number };

const TIMEOUT_HINT = "The query hit the time limit. Narrow it with a WHERE clause or a LIMIT.";
const GUARD_HINT = "Only one read-only SELECT (or WITH … SELECT) over the marts.<table> tables runs here.";
const POSITION_HINT = "Postgres stopped at the highlighted spot in the editor.";
// The guard's reasons start with their rule's code (`[guard:not_select] …`), which is for logs, not people.
const GUARD_RULE_PREFIX = /^\[guard:[^\]]*\]\s*/;
const RERUN_OPACITY = 0.5;
const ERROR_TITLES: Record<string, string> = {
  guard_rejected: "Query not allowed",
  timeout: "Query timed out",
  busy: "Too many queries running",
  too_many_connections: "Warehouse connections full",
  pii_column: "Masked column",
};

// One panel per worksheet, all kept mounted: each has its own editor history and its own run.
export const WorksheetPanel = memo(function WorksheetPanel({
  worksheet,
  activeTable,
  onSqlChange,
  onSettled,
  onRunStarted,
}: PanelProps) {
  const { id, sql: initialSql, runOnOpen } = worksheet;
  const { status, response, error, elapsedSeconds, run, cancel } = useWorksheetRun(id, onSettled);
  const [origin, setOrigin] = useState<Origin>({ sql: "", offset: 0 });
  // `view` is what the rows shown have; a server re-run's view shows only while it runs, so a failed or cancelled
  // one falls back to the last applied view.
  const [view, setView] = useState<ResultView>(EMPTY_VIEW);
  const [requested, setRequested] = useState<ResultView>();
  const shownView = status === "running" && requested ? requested : view;
  const catalog = useCatalog();
  const masked = useMemo(() => maskedColumnNames(catalog.data), [catalog.data]);

  // A Preview worksheet runs once when it opens. The timer keeps StrictMode's mount-unmount-mount probe from
  // starting (and then cancelling) a first run: only the last mount's timer fires.
  useEffect(() => {
    if (!runOnOpen) return;
    const timer = setTimeout(() => {
      onRunStarted(id);
      setOrigin({ sql: initialSql, offset: 0 });
      void run(initialSql);
    }, 0);
    return () => clearTimeout(timer);
  }, [runOnOpen, initialSql, run, onRunStarted, id]);

  // Sorting and filtering a truncated result re-runs the SQL of the last run, not the editor's current text.
  const changeView = useCallback(
    (next: ResultView) => {
      if (!response || !isServerSide(response)) {
        setView(next);
        return;
      }
      setRequested(next);
      void run(origin.sql, { ...toServerOptions(next, response.columns), onApplied: () => setView(next) });
    },
    [response, origin.sql, run],
  );

  const highlight = useMemo(
    () =>
      status === "error" && error?.detail?.position != null
        ? errorHighlight(origin.sql, error.detail.position, origin.offset)
        : undefined,
    [status, error, origin],
  );

  return (
    <Stack gap="sm">
      <SqlEditor
        value={worksheet.sql}
        onChange={(sql) => onSqlChange(id, sql)}
        running={status === "running"}
        elapsedSeconds={elapsedSeconds}
        highlight={highlight}
        activeTable={activeTable}
        onRun={(sql, offset) => {
          setOrigin({ sql, offset });
          setView(EMPTY_VIEW);
          setRequested(undefined);
          void run(sql);
        }}
        onCancel={cancel}
      />
      {status === "cancelled" && (
        <Text size="sm" c="dimmed">
          Cancelled after {elapsedSeconds} s
        </Text>
      )}
      {status === "error" && error && (
        <Alert color="red" title={ERROR_TITLES[error.code] ?? "Query failed"}>
          <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
            {error.message.replace(GUARD_RULE_PREFIX, "")}
          </Text>
          {error.code === "timeout" && <Text size="sm">{TIMEOUT_HINT}</Text>}
          {error.code === "guard_rejected" && <Text size="sm">{GUARD_HINT}</Text>}
          {highlight && <Text size="sm">{POSITION_HINT}</Text>}
        </Alert>
      )}
      {/* A Run clears the result; a server re-run keeps it, dimmed, so the grid (its scroll, the filter bar) stays. */}
      {response && (
        <Box aria-busy={status === "running"} style={{ opacity: status === "running" ? RERUN_OPACITY : 1 }}>
          <ResultGrid response={response} view={shownView} onViewChange={changeView} masked={masked} />
        </Box>
      )}
    </Stack>
  );
});
