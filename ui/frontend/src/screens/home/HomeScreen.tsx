import { Alert, Button, Loader, Stack, Text, Title } from "@mantine/core";

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
// What a first-time visitor needs (UI spec §6.2, "When something is missing").
const NO_DATA_ADVICE =
  "no orders in the marts yet: run make build-prod (first make backfill if uv run sherpa-generate status shows no simulation)";

export function HomeScreen() {
  const home = useHome();
  const { data } = home;

  return (
    <Stack gap="lg" style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
      {data === undefined && home.isError && (
        <LoadError
          title="Could not load Home"
          error={home.error}
          onRetry={() => void home.refetch()}
        />
      )}
      {data === undefined && !home.isError && (
        <Loader size="sm" role="status" aria-label="Loading" />
      )}
      {data !== undefined && (
        <>
          {data.figures && <Heading asOf={data.status.as_of_date} />}
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
              retrying={home.isFetching}
            />
          )}
        </>
      )}
    </Stack>
  );
}

// The window every tile and the chart's recent weeks speak of; only shown with figures, so an empty or failed Home
// does not claim one.
function Heading({ asOf }: { asOf: string | null }) {
  return (
    <Stack gap={4}>
      <Title order={2}>Last 30 days</Title>
      {asOf !== null && (
        <Text size="sm" c="dimmed">
          to {asOf}, compared with the 30 days before
        </Text>
      )}
    </Stack>
  );
}

function FiguresProblem({
  error,
  onRetry,
  retrying,
}: {
  error: FiguresUnavailable;
  onRetry: () => void;
  retrying: boolean;
}) {
  if (EMPTY_CODES.includes(error.code)) {
    return (
      <Alert color="blue" title="No data yet">
        <Text size="sm">{NO_DATA_ADVICE}</Text>
      </Alert>
    );
  }
  return (
    <Alert color="red" title="The figures are unavailable">
      <Text size="sm">{error.message}</Text>
      <Button
        mt="xs"
        size="xs"
        variant="light"
        color="red"
        loading={retrying}
        onClick={onRetry}
      >
        Retry
      </Button>
    </Alert>
  );
}
