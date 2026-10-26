import { Alert, Button, Code, Group, Text } from "@mantine/core";

const UNREACHABLE = "Could not reach the server.";

// A catalog request that failed. An API error answer (the hooks' ApiRequestError) shows its message and code; a
// request that got no answer at all says so instead of the browser's "Failed to fetch".
export function LoadError({ title, error, onRetry }: { title: string; error: Error | null; onRetry: () => void }) {
  const code = error !== null && "code" in error && typeof error.code === "string" ? error.code : undefined;
  return (
    <Alert color="red" title={title}>
      <Text size="sm">{code !== undefined ? error?.message : UNREACHABLE}</Text>
      <Group gap="xs" mt="xs">
        <Button size="xs" variant="light" color="red" onClick={onRetry}>
          Retry
        </Button>
        {code !== undefined && <Code>{code}</Code>}
      </Group>
    </Alert>
  );
}
