import { Alert, Anchor, Button, List, Text } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { IconAlertTriangle, IconCircleX } from "@tabler/icons-react";
import { useId, type ReactNode } from "react";
import { Link } from "react-router-dom";

import type { components } from "../../api/schema";
import { formatBuildStart } from "../../components/time";
import { toSearchParams } from "../lineage/url";

type DataStatus = components["schemas"]["DataStatus"];
type SourceFreshness = components["schemas"]["SourceFreshness"];

interface Problem {
  severity: "error" | "warning";
  key: string;
  content: ReactNode;
}

const ICON_SIZE = 18;
const WARNING_ICON =
  "light-dark(var(--mantine-color-orange-9), var(--mantine-color-yellow-4))";
// Links on the light red tint miss AA in the app's default link colour.
const LINK_COLOUR =
  "light-dark(var(--mantine-color-blue-9), var(--mantine-color-blue-4))";
// The box shows this many problems and folds the rest.
const VISIBLE_PROBLEMS = 3;
const FAILING_LINK = `/lineage?${toSearchParams({
  select: "",
  layers: undefined,
  failingOnly: true,
  node: undefined,
  column: undefined,
})}`;

// Every item shows itself or the reason it is missing (UI spec §6.2), so one failed dependency blanks nothing else.
// A problem goes in the box under the heading, a healthy fact in the footer: each item lives in one place.
export function DataProblems({ status }: { status: DataStatus }) {
  const problems = problemsOf(status);
  const [open, { toggle }] = useDisclosure(false);
  const listId = useId();
  if (problems.length === 0) return null;

  const hasError = problems.some(({ severity }) => severity === "error");
  const shown = open ? problems : problems.slice(0, VISIBLE_PROBLEMS);
  const folded = problems.length - VISIBLE_PROBLEMS;
  return (
    // Alert names itself by its title, which would override a label on it, so the region is the wrapper: it is a
    // landmark, not a live alert announced on every load and refetch.
    <div role="region" aria-label="Data problems">
      <Alert
        variant="light"
        role="presentation"
        color={hasError ? "red" : "yellow"}
        icon={
          hasError ? (
            <IconCircleX size={ICON_SIZE} aria-hidden />
          ) : (
            <IconAlertTriangle
              size={ICON_SIZE}
              color={hasError ? undefined : WARNING_ICON}
              aria-hidden
            />
          )
        }
        title={hasError ? "Data errors" : "Data warnings"}
        // Yellow on its own tint is 2.7:1: the warning title takes the theme's text colour and its icon a darker orange.
        styles={
          hasError
            ? undefined
            : { title: { color: "var(--mantine-color-text)" } }
        }
      >
        {/* The text keeps the theme's colour: only the icon and the tint carry the severity, since coloured text would
          miss AA on one of the themes. */}
        <List id={listId} size="sm" spacing={2} listStyleType="none">
          {shown.map(({ key, content }) => (
            <List.Item key={key}>{content}</List.Item>
          ))}
        </List>
        {folded > 0 && (
          <Button
            mt="xs"
            size="compact-xs"
            variant="subtle"
            color="gray"
            onClick={toggle}
            aria-expanded={open}
            aria-controls={listId}
          >
            {open ? "Show less" : `Show ${folded} more`}
          </Button>
        )}
      </Alert>
    </div>
  );
}

export function DataFooter({ status }: { status: DataStatus }) {
  const facts = factsOf(status);
  if (facts.length === 0) return null;

  return (
    <Text size="xs" c="dimmed" role="group" aria-label="Data status">
      {facts.map((fact, index) => (
        <span key={fact}>
          {index > 0 && " · "}
          <span>{fact}</span>
        </span>
      ))}
    </Text>
  );
}

// Errors first, each group in the order the items are produced.
function problemsOf(status: DataStatus): Problem[] {
  const all = [...buildProblems(status), ...freshnessProblems(status)];
  return [
    ...all.filter(({ severity }) => severity === "error"),
    ...all.filter(({ severity }) => severity === "warning"),
  ];
}

function buildProblems({ build, build_note: note }: DataStatus): Problem[] {
  if (build === null)
    return note === null
      ? []
      : [{ severity: "warning", key: "build-note", content: note }];
  const problems: Problem[] = [];
  const { failed_nodes: failed, failing_tests: failing } = build;
  if (failed > 0) {
    problems.push({
      severity: "error",
      key: "failed-nodes",
      content: failingLink(
        `${failed} failed ${failed === 1 ? "node" : "nodes"}`,
      ),
    });
  }
  if (failing > 0) {
    problems.push({
      severity: "error",
      key: "failing-tests",
      content: failingLink(
        `${failing} failing ${failing === 1 ? "test" : "tests"}`,
      ),
    });
  }
  return problems;
}

function freshnessProblems({
  freshness,
  freshness_note: note,
}: DataStatus): Problem[] {
  if (freshness === null)
    return note === null
      ? []
      : [{ severity: "warning", key: "freshness-note", content: note }];
  return freshness.problems.map((source) => ({
    severity: source.status === "error" ? "error" : "warning",
    key: source.name,
    content: `${describe(source)} (checked ${formatBuildStart(freshness.checked_at)})`,
  }));
}

function factsOf({ build, freshness }: DataStatus): string[] {
  const facts: string[] = [];
  if (build !== null) {
    facts.push(
      `Last full build ${formatBuildStart(build.started_at)} · ${build.node_count} nodes`,
    );
    if (build.failing_tests === 0) facts.push("No failing tests");
  }
  if (freshness !== null && freshness.problems.length === 0) {
    facts.push(
      `Sources fresh (checked ${formatBuildStart(freshness.checked_at)})`,
    );
  }
  return facts;
}

function failingLink(label: string): ReactNode {
  return (
    <Anchor component={Link} to={FAILING_LINK} size="sm" c={LINK_COLOUR}>
      {label}
    </Anchor>
  );
}

function describe(source: SourceFreshness): string {
  const word = source.status === "error" ? "error" : "warning";
  const loaded =
    source.hours_ago === null
      ? "never loaded"
      : `last loaded ${source.hours_ago} h ago`;
  return `${source.name}: ${word}, ${loaded}`;
}
