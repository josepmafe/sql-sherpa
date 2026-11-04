import { Anchor, Group, Text } from "@mantine/core";
import { Link } from "react-router-dom";

import type { components } from "../../api/schema";
import { formatBuildStart } from "../../components/time";
import { toSearchParams } from "../lineage/url";

type DataStatus = components["schemas"]["DataStatus"];
type SourceFreshness = components["schemas"]["SourceFreshness"];

const FAILING_LINK = `/lineage?${toSearchParams({
  select: "",
  layers: undefined,
  failingOnly: true,
  node: undefined,
  column: undefined,
})}`;

// Every item shows itself or the reason it is missing (UI spec §6.2), so one failed dependency blanks nothing else.
export function DataStatusStrip({ status }: { status: DataStatus }) {
  return (
    <Group gap="md" aria-label="Data status" role="group">
      {status.as_of_date !== null && (
        <Text size="sm">Data as of {status.as_of_date}</Text>
      )}
      <BuildItems status={status} />
      <FreshnessItems status={status} />
    </Group>
  );
}

function BuildItems({ status }: { status: DataStatus }) {
  const { build, build_note: note } = status;
  if (build === null)
    return note === null ? null : <Text size="sm">{note}</Text>;
  const failing = build.failing_tests;
  return (
    <>
      <Text size="sm">
        Last full build {formatBuildStart(build.started_at)} ·{" "}
        {build.node_count} nodes
      </Text>
      {failing === 0 ? (
        <Text size="sm">No failing tests</Text>
      ) : (
        <Anchor component={Link} to={FAILING_LINK} size="sm">
          {failing} failing {failing === 1 ? "test" : "tests"}
        </Anchor>
      )}
    </>
  );
}

function FreshnessItems({ status }: { status: DataStatus }) {
  const { freshness, freshness_note: note } = status;
  if (freshness === null)
    return note === null ? null : <Text size="sm">{note}</Text>;
  if (freshness.problems.length === 0) {
    return (
      <Text size="sm">
        Sources fresh (checked {formatBuildStart(freshness.checked_at)})
      </Text>
    );
  }
  return (
    <>
      {freshness.problems.map((source) => (
        <Text
          key={source.name}
          size="sm"
          c={source.status === "error" ? "red.8" : "orange.9"}
        >
          {describe(source)}
        </Text>
      ))}
    </>
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
