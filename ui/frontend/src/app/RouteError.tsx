import { Alert, Button, Stack, Text, Title } from "@mantine/core";
import { isRouteErrorResponse, useRouteError } from "react-router-dom";

// The root route's errorElement: a screen that throws (or an unknown path) shows this, not React Router's developer page.
export function RouteError() {
  const error = useRouteError();
  const reason = isRouteErrorResponse(error)
    ? `${error.status} ${error.statusText}`
    : error instanceof Error
      ? error.message
      : String(error);

  return (
    <Stack p="md" maw={640}>
      <Title order={3}>Something went wrong</Title>
      <Alert color="red">
        <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
          {reason}
        </Text>
      </Alert>
      <Button w="fit-content" onClick={() => window.location.reload()}>
        Reload
      </Button>
    </Stack>
  );
}
