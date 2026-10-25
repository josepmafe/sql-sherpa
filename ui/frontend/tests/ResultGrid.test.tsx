import { MantineProvider } from "@mantine/core";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { ResultGrid } from "../src/components/ResultGrid";
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

function Harness({ data }: { data: QueryResponse }) {
  const [view, setView] = useState<ResultView>(EMPTY_VIEW);
  return (
    <MantineProvider env="test">
      <ResultGrid response={data} view={view} onViewChange={setView} />
    </MantineProvider>
  );
}

const firstColumn = () =>
  screen
    .getAllByRole("row")
    .slice(1)
    .map((row) => within(row).getAllByRole("cell")[0]?.textContent);

describe("ResultGrid", () => {
  it("sorts numeric strings numerically", async () => {
    const user = userEvent.setup();
    render(<Harness data={response()} />);

    await user.click(screen.getByRole("columnheader", { name: "amount" }));

    expect(firstColumn()).toEqual(["c", "b", "a"]);
    await user.click(screen.getByRole("columnheader", { name: "amount" }));
    expect(firstColumn()).toEqual(["a", "b", "c"]);
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
