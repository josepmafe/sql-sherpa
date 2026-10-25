import { Alert, Stack, Text } from "@mantine/core";
import { useEffect, useMemo, useState } from "react";

import { ResultGrid } from "../../components/ResultGrid";
import { EMPTY_VIEW, isServerSide, type ResultView, toServerOptions } from "../../components/resultView";
import { errorHighlight, SqlEditor } from "../../components/SqlEditor";
import { useWorksheetRun, type RunOutcome } from "./useWorksheetRun";
import type { Worksheet } from "./worksheetStore";

type PanelProps = {
  worksheet: Worksheet;
  activeTable?: string;
  onSqlChange: (sql: string) => void;
  onSettled: (outcome: RunOutcome) => void;
  onRunStarted: () => void;
};

// What was sent and where it started in the editor: an error position counts from the start of the sent text.
type Origin = { sql: string; offset: number };

const TIMEOUT_HINT = "The query hit the time limit. Narrow it with a WHERE clause or a LIMIT.";
const ERROR_TITLES: Record<string, string> = { guard_rejected: "Query not allowed", timeout: "Query timed out" };

// One panel per worksheet, all kept mounted: each has its own editor history and its own run.
export function WorksheetPanel({ worksheet, activeTable, onSqlChange, onSettled, onRunStarted }: PanelProps) {
  const { status, response, error, elapsedSeconds, run, cancel } = useWorksheetRun(worksheet.id, onSettled);
  const [origin, setOrigin] = useState<Origin>({ sql: "", offset: 0 });
  const [view, setView] = useState<ResultView>(EMPTY_VIEW);

  // A Preview worksheet runs once when it opens. The timer keeps StrictMode's mount-unmount-mount probe from
  // starting (and then cancelling) a first run: only the last mount's timer fires.
  const { sql: initialSql, runOnOpen } = worksheet;
  useEffect(() => {
    if (!runOnOpen) return;
    const timer = setTimeout(() => {
      onRunStarted();
      setOrigin({ sql: initialSql, offset: 0 });
      void run(initialSql);
    }, 0);
    return () => clearTimeout(timer);
  }, [runOnOpen, initialSql, run, onRunStarted]);

  // Sorting and filtering a truncated result re-runs the SQL of the last run, not the editor's current text.
  const changeView = (next: ResultView) => {
    setView(next);
    if (response && isServerSide(response)) void run(origin.sql, toServerOptions(next, response.columns));
  };

  const highlight = useMemo(
    () =>
      status === "error" && error?.position !== undefined
        ? errorHighlight(origin.sql, error.position, origin.offset)
        : undefined,
    [status, error, origin],
  );

  return (
    <Stack gap="sm">
      <SqlEditor
        value={worksheet.sql}
        onChange={onSqlChange}
        running={status === "running"}
        elapsedSeconds={elapsedSeconds}
        highlight={highlight}
        activeTable={activeTable}
        onRun={(sql, offset) => {
          setOrigin({ sql, offset });
          setView(EMPTY_VIEW);
          void run(sql);
        }}
        onCancel={cancel}
      />
      {status === "done" && response && <ResultGrid response={response} view={view} onViewChange={changeView} />}
      {status === "cancelled" && (
        <Text size="sm" c="dimmed">
          Cancelled after {elapsedSeconds} s
        </Text>
      )}
      {status === "error" && error && (
        <Alert color="red" title={ERROR_TITLES[error.code] ?? "Query failed"}>
          <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
            {error.message}
          </Text>
          {error.code === "timeout" && <Text size="sm">{TIMEOUT_HINT}</Text>}
        </Alert>
      )}
    </Stack>
  );
}
