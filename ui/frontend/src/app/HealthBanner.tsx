import { Alert, Stack } from "@mantine/core";
import { IconAlertTriangle } from "@tabler/icons-react";

import { useHealth } from "../api/queries";

// One banner per dependency that is down; screens keep working for everything else (UI spec §3).
export function HealthBanner() {
  const { data, isError } = useHealth();
  const problems: string[] = [];

  if (isError) {
    problems.push("The SQL Sherpa server is unreachable.");
  }
  if (data?.database === "down") {
    problems.push("Database unavailable: queries and the table preview cannot run.");
  }
  if (data?.artifacts.status === "missing") {
    problems.push("dbt artifacts missing: run `make build-prod` to see table descriptions and lineage.");
  }

  if (problems.length === 0) return null;

  return (
    <Stack gap="xs" mb="md">
      {problems.map((message) => (
        <Alert key={message} color="red" icon={<IconAlertTriangle size={18} />} role="alert">
          {message}
        </Alert>
      ))}
    </Stack>
  );
}
