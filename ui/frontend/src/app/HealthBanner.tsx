import { Alert, Stack } from "@mantine/core";
import { IconAlertTriangle } from "@tabler/icons-react";

import { useHealth } from "../api/queries";

// Red for what stops the screens' queries, yellow for a degraded one that leaves them running.
type Problem = { message: string; color: "red" | "yellow" };

// One banner per dependency that is down; screens keep working for everything else (UI spec §3).
export function HealthBanner() {
  const { data, isError } = useHealth();
  const problems: Problem[] = [];

  if (isError) {
    problems.push({ message: "The SQL Sherpa server is unreachable.", color: "red" });
  }
  if (data?.database === "down") {
    problems.push({ message: "Database unavailable: queries and the table preview cannot run.", color: "red" });
  }
  if (data?.artifacts.status === "missing") {
    problems.push({
      message: "dbt artifacts missing: run `make build-prod` to see table descriptions and lineage.",
      color: "yellow",
    });
  }

  if (problems.length === 0) return null;

  return (
    <Stack gap="xs" mb="md">
      {problems.map(({ message, color }) => (
        <Alert key={message} color={color} icon={<IconAlertTriangle size={18} />} role="alert" py={8}>
          {message}
        </Alert>
      ))}
    </Stack>
  );
}
