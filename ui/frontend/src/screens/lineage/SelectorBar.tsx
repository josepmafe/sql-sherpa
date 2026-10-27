import { Autocomplete, Button, Group, SegmentedControl, Select, Stack, Text, TextInput } from "@mantine/core";
import { useClipboard } from "@mantine/hooks";
import { useState } from "react";

import { ApiRequestError, useLineage } from "../../api/queries";
import { type Depth, domainSelector, pathSelector, readMode, type SelectorMode } from "./selectors";

type Mode = Exclude<SelectorMode["mode"], "custom">;

const MODE_LABELS: { value: Mode; label: string }[] = [
  { value: "project", label: "Whole project" },
  { value: "path", label: "Table path" },
  { value: "domain", label: "Domain" },
];
const DEPTH_OPTIONS = ["all", "1", "2", "3", "4", "5"];
const DEFAULT_DEPTH: Depth = "all";
const WHOLE_PROJECT = { select: "", layers: undefined, failingOnly: false } as const;
const UNSUPPORTED_SELECTOR = "unsupported_selector";

interface SelectorBarProps {
  select: string;
  onSelect: (select: string) => void;
  // The graph request's error: only an unsupported selector shows here.
  error: Error | null;
}

export function SelectorBar({ select, onSelect, error }: SelectorBarProps) {
  // The names and domains to pick from come from the whole-project graph, which the screen's own request shares
  // while the selector is empty.
  const { data: project } = useLineage(WHOLE_PROJECT);
  const current = readMode(select);
  // A mode chosen before it has a selector to write (no table picked yet) is held here.
  const [chosen, setChosen] = useState<Mode>();
  const mode: Mode | undefined = chosen ?? (current.mode === "custom" ? undefined : current.mode);
  const names = unique(project?.nodes.filter((node) => node.resource_type !== "source").map((node) => node.name));
  const domains = unique(project?.nodes.map((node) => node.domain));
  const path = current.mode === "path" ? current : undefined;
  const [boxText, setBoxText] = useState(select);
  const [syncedSelect, setSyncedSelect] = useState(select);
  const clipboard = useClipboard();
  // The box follows the URL (back/forward, a mode control) unless the user is mid-typing a new selector.
  if (select !== syncedSelect) {
    setSyncedSelect(select);
    setBoxText(select);
    setChosen(undefined);
  }

  const apply = (next: string) => {
    setChosen(undefined);
    onSelect(next);
  };
  const switchMode = (next: string) => {
    switch (next as Mode) {
      case "project":
        return apply("");
      case "path":
        // The table already chosen stays: the selector is not rewritten.
        return setChosen("path");
      case "domain":
        return domains[0] !== undefined ? apply(domainSelector(domains[0])) : setChosen("domain");
    }
  };

  return (
    <Stack gap="xs">
      <Group align="flex-end" gap="md">
        <SegmentedControl aria-label="Selection mode" data={MODE_LABELS} value={mode ?? ""} onChange={switchMode} />
        {mode === "path" && (
          <>
            <Autocomplete
              label="Table"
              placeholder="Table name"
              data={names}
              key={path?.name ?? ""}
              defaultValue={path?.name ?? ""}
              onOptionSubmit={(name) => apply(pathSelector(name, path?.up ?? DEFAULT_DEPTH, path?.down ?? DEFAULT_DEPTH))}
            />
            <DepthSelect
              label="Upstream"
              value={path?.up}
              disabled={path === undefined}
              onChange={(up) => path !== undefined && apply(pathSelector(path.name, up, path.down))}
            />
            <DepthSelect
              label="Downstream"
              value={path?.down}
              disabled={path === undefined}
              onChange={(down) => path !== undefined && apply(pathSelector(path.name, path.up, down))}
            />
          </>
        )}
        {mode === "domain" && (
          <Select
            label="Domain"
            data={domains}
            value={current.mode === "domain" ? current.domain : null}
            onChange={(domain) => domain !== null && apply(domainSelector(domain))}
          />
        )}
      </Group>
      <Group align="flex-start" gap="xs">
        <TextInput
          aria-label="dbt selector"
          placeholder="dbt selector, e.g. +fct_orders+"
          style={{ flex: 1 }}
          value={boxText}
          onChange={(event) => setBoxText(event.currentTarget.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") apply(boxText.trim());
          }}
          error={unsupportedMessage(error)}
          errorProps={{ role: "alert" }}
        />
        <Button variant="light" onClick={() => clipboard.copy(select)}>
          {clipboard.copied ? "Copied" : "Copy dbt selector"}
        </Button>
      </Group>
      {mode === undefined && (
        <Text size="xs" c="dimmed">
          A custom selector: none of the three modes writes it.
        </Text>
      )}
    </Stack>
  );
}

function DepthSelect(props: {
  label: string;
  value: Depth | undefined;
  disabled: boolean;
  onChange: (depth: Depth) => void;
}) {
  return (
    <Select
      label={props.label}
      w={100}
      data={DEPTH_OPTIONS}
      allowDeselect={false}
      disabled={props.disabled}
      value={String(props.value ?? DEFAULT_DEPTH)}
      onChange={(value) => value !== null && props.onChange(value === "all" ? "all" : (Number(value) as Depth))}
    />
  );
}

function unsupportedMessage(error: Error | null): string | undefined {
  return error instanceof ApiRequestError && error.code === UNSUPPORTED_SELECTOR ? error.message : undefined;
}

function unique(values: (string | null | undefined)[] | undefined): string[] {
  return [...new Set((values ?? []).filter((value): value is string => value != null))].sort();
}
