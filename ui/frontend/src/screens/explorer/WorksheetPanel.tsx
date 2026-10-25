import { Alert, Stack, Text } from "@mantine/core";
import { useMemo, useState } from "react";

import { errorHighlight, SqlEditor } from "../../components/SqlEditor";
import { useWorksheetRun, type RunOutcome } from "./useWorksheetRun";
import type { Worksheet } from "./worksheetStore";

type PanelProps = {
  worksheet: Worksheet;
  activeTable?: string;
  onSqlChange: (sql: string) => void;
  onSettled: (outcome: RunOutcome) => void;
};

// What was sent and where it started in the editor: an error position counts from the start of the sent text.
type Origin = { sql: string; offset: number };

// One panel per worksheet, all kept mounted: each has its own editor history and its own run.
export function WorksheetPanel({ worksheet, activeTable, onSqlChange, onSettled }: PanelProps) {
  const { status, response, error, elapsedSeconds, run, cancel } = useWorksheetRun(worksheet.id, onSettled);
  const [origin, setOrigin] = useState<Origin>({ sql: "", offset: 0 });

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
          void run(sql);
        }}
        onCancel={cancel}
      />
      {status === "done" && response && (
        <Text size="sm" c="dimmed">
          {response.returned_rows} rows in {response.elapsed_ms} ms
        </Text>
      )}
      {status === "cancelled" && (
        <Text size="sm" c="dimmed">
          Cancelled after {elapsedSeconds} s
        </Text>
      )}
      {status === "error" && error && (
        <Alert color="red" title={error.code === "guard_rejected" ? "Query not allowed" : "Query failed"}>
          <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
            {error.message}
          </Text>
        </Alert>
      )}
    </Stack>
  );
}
