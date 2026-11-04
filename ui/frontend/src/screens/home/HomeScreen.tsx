import { Alert, Button, Code, Loader, Stack, Text, Title } from "@mantine/core";

import { useHome } from "../../api/queries";
import type { components } from "../../api/schema";
import { LoadError } from "../explorer/LoadError";
import { DataStatusStrip } from "./DataStatusStrip";
import { Tiles } from "./Tiles";
import { TrendChart } from "./TrendChart";

type FiguresUnavailable = components["schemas"]["FiguresUnavailable"];

// The codes that mean the warehouse holds nothing to show yet, not that the query failed.
const EMPTY_CODES: readonly FiguresUnavailable["code"][] = [
  "no_data",
  "catalog_unavailable",
];
const BUILD_COMMAND = "make backfill && make build-prod";

export function HomeScreen() {
  const home = useHome();
  const { data } = home;

  return (
    <Stack gap="md" style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
      <Title order={2}>Home</Title>
      {data === undefined && home.isError && (
        <LoadError
          title="Could not load Home"
          error={home.error}
          onRetry={() => void home.refetch()}
        />
      )}
      {data === undefined && !home.isError && (
        <Loader size="sm" aria-label="Loading" />
      )}
      {data !== undefined && (
        <>
          <DataStatusStrip status={data.status} />
          {data.figures && (
            <>
              <Tiles tiles={data.figures.tiles} />
              <TrendChart weeks={data.figures.trend} />
            </>
          )}
          {data.figures_error && (
            <FiguresProblem
              error={data.figures_error}
              onRetry={() => void home.refetch()}
            />
          )}
        </>
      )}
    </Stack>
  );
}

function FiguresProblem({
  error,
  onRetry,
}: {
  error: FiguresUnavailable;
  onRetry: () => void;
}) {
  if (EMPTY_CODES.includes(error.code)) {
    return (
      <Alert color="blue" title="No data yet">
        <Text size="sm">
          The warehouse has nothing to show. Build it with{" "}
          <Code>{BUILD_COMMAND}</Code>.
        </Text>
      </Alert>
    );
  }
  return (
    <Alert color="red" title="The figures are unavailable">
      <Text size="sm">{error.message}</Text>
      <Button mt="xs" size="xs" variant="light" color="red" onClick={onRetry}>
        Retry
      </Button>
    </Alert>
  );
}
