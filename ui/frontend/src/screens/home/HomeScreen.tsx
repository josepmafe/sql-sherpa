import { Loader, Stack, Title } from "@mantine/core";

import { useHome } from "../../api/queries";
import { Tiles } from "./Tiles";
import { TrendChart } from "./TrendChart";

export function HomeScreen() {
  const { data } = useHome();

  return (
    <Stack gap="md" style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
      <Title order={2}>Home</Title>
      {data === undefined && <Loader size="sm" aria-label="Loading" />}
      {data?.figures && (
        <>
          <Tiles tiles={data.figures.tiles} />
          <TrendChart weeks={data.figures.trend} />
        </>
      )}
    </Stack>
  );
}
