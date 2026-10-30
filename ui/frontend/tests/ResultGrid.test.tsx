import { MantineProvider } from "@mantine/core";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { MASKED_HINT, ResultGrid } from "../src/components/ResultGrid";
import { EMPTY_VIEW, type QueryResponse, type ResultView } from "../src/components/resultView";

function response(overrides: Partial<QueryResponse> = {}): QueryResponse {
  return {
    query_id: "q",
    columns: ["name", "amount"],
    column_types: ["text", "number"],
    rows: [
      ["a", "10"],
      ["b", "9"],
      ["c", "2.5"],
    ],
    returned_rows: 3,
    truncated: false,
    elapsed_ms: 12,
    mode: "client",
    ...overrides,
  };
}

function Harness({ data, masked = new Set() }: { data: QueryResponse; masked?: ReadonlySet<string> }) {
  const [view, setView] = useState<ResultView>(EMPTY_VIEW);
  return (
    <MantineProvider env="test">
      <ResultGrid response={data} view={view} onViewChange={setView} masked={masked} />
    </MantineProvider>
  );
}

const firstColumn = () =>
  screen
    .getAllByRole("row")
    .slice(1)
    .map((row) => within(row).getAllByRole("cell")[0]?.textContent);

describe("ResultGrid", () => {
  it("sorts by one column at a time, also on shift-click", async () => {
    const user = userEvent.setup();
    render(<Harness data={response()} />);
    await user.click(screen.getByRole("button", { name: "amount" }));

    await user.keyboard("{Shift>}");
    await user.click(screen.getByRole("button", { name: "name" }));

    expect(screen.getByRole("columnheader", { name: "name" })).toHaveAttribute("aria-sort", "ascending");
    expect(screen.getByRole("columnheader", { name: "amount" })).toHaveAttribute("aria-sort", "none");
    expect(firstColumn()).toEqual(["a", "b", "c"]);
  });

  it("sorts from the keyboard: each header holds a button", async () => {
    const user = userEvent.setup();
    render(<Harness data={response()} />);

    screen.getByRole("button", { name: "amount" }).focus();
    await user.keyboard("{Enter}");

    expect(firstColumn()).toEqual(["c", "b", "a"]);
    expect(screen.getByRole("columnheader", { name: "amount" })).toHaveAttribute("aria-sort", "ascending");
    await user.keyboard(" ");
    expect(screen.getByRole("columnheader", { name: "amount" })).toHaveAttribute("aria-sort", "descending");
  });

  it("says 1 row, not 1 rows", () => {
    render(<Harness data={response({ rows: [["a", "10"]], returned_rows: 1 })} />);

    expect(screen.getByText("1 row in 12 ms")).toBeInTheDocument();
  });

  it("renders empty and null cells", () => {
    const longText = "x".repeat(5000);
    const rows = [
      [null, null],
      [longText, "1"],
    ];
    render(<Harness data={response({ rows, returned_rows: 2 })} />);

    expect(screen.getAllByText("null")).toHaveLength(2);
    expect(screen.getByTitle(longText)).toHaveTextContent(/^x{200}…$/);
    expect(screen.getByText("2 rows in 12 ms")).toBeInTheDocument();
  });

  it("shows timestamps and JSON as Postgres's text form, which contains matches", () => {
    render(
      <Harness
        data={response({
          columns: ["at", "doc"],
          column_types: ["timestamp", "other"],
          rows: [["2025-01-15T08:30:00.120000+00:00", { a: 1 }]],
          returned_rows: 1,
        })}
      />,
    );

    expect(screen.getByText("2025-01-15 08:30:00.12+00")).toBeInTheDocument();
    expect(screen.getByText('{"a": 1}')).toBeInTheDocument();
  });

  it("renders zero rows and zero columns without crashing", () => {
    const { unmount } = render(<Harness data={response({ rows: [], returned_rows: 0 })} />);
    expect(screen.getByText("No rows.")).toBeInTheDocument();
    unmount();

    render(<Harness data={response({ columns: [], column_types: [], rows: [], returned_rows: 0 })} />);
    expect(screen.getByText("The query returned no columns.")).toBeInTheDocument();
  });

  it("shows read-only, and truncated with the server banner", () => {
    render(<Harness data={response({ truncated: true, returned_rows: 1000 })} />);

    expect(screen.getByText("read-only")).toBeInTheDocument();
    expect(screen.getByText("truncated")).toBeInTheDocument();
    expect(
      screen.getByText("Truncated at 1,000 rows: sorting and filtering run on the server over all rows"),
    ).toBeInTheDocument();
  });

  it("duplicate-columns warning is shown", () => {
    render(
      <Harness
        data={response({
          columns: ["id", "id"],
          column_types: ["number", "number"],
          rows: [["1", "2"]],
          returned_rows: 1,
          truncated: true,
        })}
      />,
    );

    expect(screen.getByText(/duplicate column names: sorting and filtering apply only to the rows shown/)).toBeInTheDocument();
    expect(screen.queryByText(/run on the server/)).not.toBeInTheDocument();
  });

  it("does not allow a filter with an empty value", async () => {
    const user = userEvent.setup();
    render(<Harness data={response()} />);

    await user.click(screen.getByRole("combobox", { name: "Filter column" }));
    await user.click(await screen.findByRole("option", { name: "amount" }));

    expect(screen.getByRole("button", { name: "Add filter" })).toBeDisabled();
    expect(firstColumn()).toEqual(["a", "b", "c"]);
  });

  it("refuses a filter value Postgres would not read as the column's type", async () => {
    const user = userEvent.setup();
    const typed = response({
      columns: ["amount", "day", "active", "name"],
      column_types: ["number", "date", "boolean", "text"],
      rows: [["10", "2025-01-15", true, "a"]],
      returned_rows: 1,
    });
    render(<Harness data={typed} />);
    const tryValue = async (column: string, value: string) => {
      await user.click(screen.getByRole("combobox", { name: "Filter column" }));
      await user.click(await screen.findByRole("option", { name: column }));
      const input = screen.getByRole("textbox", { name: "Filter value" });
      await user.clear(input);
      await user.type(input, value);
    };
    const addFilter = () => screen.getByRole("button", { name: "Add filter" });

    await tryValue("amount", "abc");
    expect(addFilter()).toBeDisabled();
    expect(screen.getByText("Enter a number, like 12.5")).toBeInTheDocument();
    await tryValue("day", "15/01/2025");
    expect(addFilter()).toBeDisabled();
    expect(screen.getByText("Enter a date, like 2025-01-15 or 2025-01-15 10:30")).toBeInTheDocument();
    await tryValue("active", "maybe");
    expect(addFilter()).toBeDisabled();
    expect(screen.getByText("Enter true or false")).toBeInTheDocument();
    await tryValue("amount", " ");
    expect(addFilter()).toBeDisabled();

    await tryValue("active", "yes");
    expect(addFilter()).toBeEnabled();
    await tryValue("name", "abc");
    expect(addFilter()).toBeEnabled();
  });

  it("does not check the value of a contains filter, which matches text", async () => {
    const user = userEvent.setup();
    render(<Harness data={response()} />);

    await user.click(screen.getByRole("combobox", { name: "Filter column" }));
    await user.click(await screen.findByRole("option", { name: "amount" }));
    await user.click(screen.getByRole("combobox", { name: "Filter operator" }));
    await user.click(await screen.findByRole("option", { name: "contains" }));
    await user.type(screen.getByRole("textbox", { name: "Filter value" }), ".5");

    expect(screen.getByRole("button", { name: "Add filter" })).toBeEnabled();
  });

  it("offers no sorting or filtering by a masked column, matched by name in any case", async () => {
    const user = userEvent.setup();
    const masked = response({
      columns: ["name", "Email"],
      column_types: ["text", "text"],
      rows: [
        ["b", "***"],
        ["a", "***"],
      ],
      returned_rows: 2,
    });
    render(<Harness data={masked} masked={new Set(["email"])} />);

    await user.click(screen.getByRole("columnheader", { name: "Email" }));
    await user.click(screen.getByRole("combobox", { name: "Filter column" }));

    expect(screen.getByRole("columnheader", { name: "Email" })).toHaveAttribute("aria-sort", "none");
    expect(screen.getByTitle(MASKED_HINT)).toHaveTextContent("Email");
    expect(screen.queryByRole("button", { name: "Email" })).not.toBeInTheDocument();
    expect(await screen.findByRole("option", { name: "Email (masked)" })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("option", { name: "name" })).not.toHaveAttribute("aria-disabled");
  });

  it("renders only a window of 1,000 rows", () => {
    const rows = Array.from({ length: 1000 }, (_, i) => [`row${i}`, String(i)]);
    render(<Harness data={response({ rows, returned_rows: 1000, truncated: true })} />);

    const rendered = screen.getAllByRole("row").length;

    expect(rendered).toBeGreaterThan(1);
    expect(rendered).toBeLessThan(100);
  });

  it("filters rows in the browser and reports the shown count", async () => {
    const user = userEvent.setup();
    render(<Harness data={response()} />);

    await user.click(screen.getByRole("combobox", { name: "Filter column" }));
    await user.click(await screen.findByRole("option", { name: "name" }));
    await user.type(screen.getByRole("textbox", { name: "Filter value" }), "b");
    await user.click(screen.getByRole("button", { name: "Add filter" }));

    expect(firstColumn()).toEqual(["b"]);
    expect(screen.getByText("1 of 3 rows in 12 ms")).toBeInTheDocument();
  });
});
