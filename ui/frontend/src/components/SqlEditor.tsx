import { completeFromList } from "@codemirror/autocomplete";
import { PostgreSQL, sql as sqlLanguage } from "@codemirror/lang-sql";
import { StateEffect, StateField, type Extension } from "@codemirror/state";
import { Decoration, EditorView, type ViewUpdate } from "@codemirror/view";
import { Box, Button, Group, Stack, Text, useComputedColorScheme } from "@mantine/core";
import CodeMirror from "@uiw/react-codemirror";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import { api } from "../api/client";
import { useCatalog } from "../api/queries";
import type { components } from "../api/schema";

type CatalogTree = components["schemas"]["CatalogTree"];

export type Highlight = { from: number; to: number };

type EditorProps = {
  value: string;
  onChange: (value: string) => void;
  running: boolean;
  // Where the last error points, in editor offsets; it clears as soon as the text changes.
  highlight?: Highlight;
  // The table selected in the catalog tree: its columns complete without a prefix.
  activeTable?: string;
  // `offset` is where the sent text starts in the editor (non-zero for "Run selection").
  onRun: (sql: string, offset: number) => void;
  onCancel: () => void;
};

const EDITOR_HEIGHT = "220px";
const FORMAT_FAILED = "Could not format the query.";

export function SqlEditor({
  value,
  onChange,
  running,
  highlight,
  activeTable,
  onRun,
  onCancel,
}: EditorProps) {
  const catalog = useCatalog();
  const view = useRef<EditorView | undefined>(undefined);
  const [hasSelection, setHasSelection] = useState(false);
  const [formatError, setFormatError] = useState<string>();
  const colorScheme = useComputedColorScheme("light");
  const empty = value.trim() === "";

  const extensions = useMemo(
    () => [sqlExtension(catalog.data, activeTable), errorHighlightField, focusOutline],
    [catalog.data, activeTable],
  );

  useEffect(() => {
    view.current?.dispatch({
      effects: setErrorHighlight.of(highlight ?? null),
    });
  }, [highlight]);

  const runSelection = () => {
    const range = view.current?.state.selection.main;
    if (!range || range.empty) return;
    onRun(view.current?.state.sliceDoc(range.from, range.to) ?? "", range.from);
  };

  // Captured before CodeMirror sees it, since its own Mod-Enter inserts a blank line.
  const onKeyDownCapture = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key !== "Enter" || !(event.ctrlKey || event.metaKey)) return;
    event.preventDefault();
    event.stopPropagation();
    if (!running && !empty) onRun(value, 0);
  };

  const format = async () => {
    setFormatError(undefined);
    try {
      const { data, error } = await api.POST("/api/queries/format", {
        body: { sql: value },
      });
      if (data) onChange(data.sql);
      else setFormatError(messageOf(error) ?? FORMAT_FAILED);
    } catch {
      setFormatError(FORMAT_FAILED);
    }
  };

  const onUpdate = (update: ViewUpdate) => {
    if (update.selectionSet || update.docChanged) setHasSelection(!update.state.selection.main.empty);
  };

  return (
    <Stack gap="xs">
      <Group gap="xs">
        {running ? (
          <Button color="red" size="xs" onClick={onCancel}>
            Cancel
          </Button>
        ) : (
          <Button size="xs" disabled={empty} onClick={() => onRun(value, 0)}>
            Run
          </Button>
        )}
        <Button size="xs" variant="default" disabled={running || !hasSelection} onClick={runSelection}>
          Run selection
        </Button>
        <Button size="xs" variant="default" disabled={running || empty} onClick={() => void format()}>
          Format
        </Button>
        <Text size="xs" c="dimmed" ml="auto">
          Ctrl/Cmd+Enter runs
        </Text>
      </Group>
      {formatError && (
        <Text size="sm" c="red">
          {formatError}
        </Text>
      )}
      <Box onKeyDownCapture={onKeyDownCapture}>
        <CodeMirror
          value={value}
          height={EDITOR_HEIGHT}
          extensions={extensions}
          theme={colorScheme}
          onChange={onChange}
          onUpdate={onUpdate}
          onCreateEditor={(created) => {
            view.current = created;
          }}
          basicSetup={{ autocompletion: true }}
          aria-label="SQL editor"
        />
      </Box>
    </Stack>
  );
}

// Postgres counts characters (code points) from 0 in the text it was sent; the editor counts UTF-16 units.
// The highlight covers the word at the position, or one character, and `baseOffset` places the sent text in the editor.
export function errorHighlight(sentSql: string, position: number, baseOffset: number): Highlight {
  const characters = Array.from(sentSql);
  const start = Math.max(0, Math.min(position, characters.length - 1));
  let end = start + 1;
  if (/\w/.test(characters[start] ?? "")) {
    while (end < characters.length && /\w/.test(characters[end] ?? "")) end += 1;
  }
  const from = baseOffset + characters.slice(0, start).join("").length;
  return { from, to: baseOffset + characters.slice(0, end).join("").length };
}

// `marts.` completes tables, `marts.<table>.` their columns. Tables are never offered bare (the guard rejects
// unqualified names), but the selected table's columns are, so `select ` can complete them.
export function sqlExtension(catalog: CatalogTree | undefined, activeTable: string | undefined): Extension {
  if (!catalog) return sqlLanguage({ dialect: PostgreSQL });
  const tables: Record<string, string[]> = {};
  for (const domain of catalog.domains) {
    for (const table of domain.tables) tables[table.name] = table.columns.map((column) => column.name);
  }
  const support = sqlLanguage({ dialect: PostgreSQL, schema: { [catalog.schema_name]: tables } });
  const columns = (activeTable ? tables[activeTable] : undefined) ?? [];
  return [
    support,
    support.language.data.of({
      autocomplete: completeFromList(columns.map((label) => ({ label, type: "property" }))),
    }),
  ];
}

// CodeMirror's own focus outline is dotted #212121, invisible on the dark theme; Mantine's focus colour shows in both.
const focusOutline = EditorView.theme({
  "&.cm-focused": { outline: "2px solid var(--mantine-primary-color-filled)" },
});

const setErrorHighlight = StateEffect.define<Highlight | null>();
// Mantine's red "light" variant colour follows the colour scheme, so the mark keeps its contrast in dark mode.
const errorMark = Decoration.mark({
  class: "cm-error-position",
  attributes: { style: "background: var(--mantine-color-red-light)" },
});

const errorHighlightField = StateField.define({
  create: () => Decoration.none,
  update(decorations, transaction) {
    if (transaction.docChanged) return Decoration.none;
    for (const effect of transaction.effects) {
      if (!effect.is(setErrorHighlight)) continue;
      const range = effect.value;
      const length = transaction.state.doc.length;
      if (!range || range.from >= length) return Decoration.none;
      return Decoration.set([errorMark.range(range.from, Math.min(range.to, length))]);
    }
    return decorations;
  },
  provide: (field) => EditorView.decorations.from(field),
});

function messageOf(error: unknown): string | undefined {
  if (typeof error === "object" && error !== null && "message" in error && typeof error.message === "string") {
    return error.message;
  }
  return undefined;
}
