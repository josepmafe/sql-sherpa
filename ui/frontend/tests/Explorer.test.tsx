import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CatalogTree } from "../src/screens/explorer/CatalogTree";
import { Inspector } from "../src/screens/explorer/Inspector";
import {
  type CatalogTree as CatalogPayload,
  catalog,
  columnDetails,
  columnSummary,
  type TableSummary,
  table,
  tableDetails,
} from "./fixtures/api";
import { renderWithProviders } from "./fixtures/render";

const CATALOG = catalog({
  domains: [
    {
      name: "sales",
      tables: [
        table({
          name: "fct_orders",
          domain: "sales",
          description: "One row per order.",
          grain: "one row per order",
          primary_key: ["order_id"],
          columns: [
            columnSummary({ name: "order_id", data_type: "bigint", description: "Order key.", is_primary_key: true }),
            columnSummary({ name: "customer_email", description: "Email.", is_pii: true }),
          ],
        }),
      ],
    },
    {
      name: "product",
      tables: [
        table({
          name: "dim_products",
          domain: "product",
          description: "One row per product.",
          grain: "one row per product",
          primary_key: ["product_id"],
          columns: [columnSummary({ name: "sku", description: "Stock keeping unit." })],
        }),
      ],
    },
  ],
});

// The catalog with fct_orders changed.
function withOrders(change: Partial<TableSummary>): CatalogPayload {
  const [sales, ...others] = CATALOG.domains;
  const [orders, ...rest] = sales!.tables;
  return { ...CATALOG, domains: [{ ...sales!, tables: [{ ...orders!, ...change }, ...rest] }, ...others] };
}

const DETAILS = tableDetails({
  description: "One row per order.",
  grain: "one row per order",
  primary_key: ["order_id"],
  synonyms: ["purchases"],
  tests: ["unique_fct_orders_order_id"],
  columns: [
    columnDetails({
      description: "Order status.",
      accepted_values: ["placed", "shipped"],
      synonyms: ["state"],
      metric_refs: [{ name: "net_revenue", definition: "Revenue after refunds." }],
    }),
  ],
});

const useCatalog = vi.fn();
const useTableDetails = vi.fn();
vi.mock("../src/api/queries", () => ({
  useCatalog: () => useCatalog(),
  useTableDetails: (name: string | undefined) => useTableDetails(name),
}));

// What the hooks throw for an API error answer (ApiRequestError): an Error with the API's code.
const apiError = (code: string, message: string) => Object.assign(new Error(message), { code, status: 503 });

describe("CatalogTree", () => {
  beforeEach(() => {
    useCatalog.mockReset().mockReturnValue({ data: CATALOG });
  });

  it("a catalog that fails to load shows the API's message and code, and Retry fetches it again", async () => {
    const user = userEvent.setup();
    const refetch = vi.fn();
    const error = apiError("catalog_unavailable", "the dbt artifacts are missing; run make build-prod");
    useCatalog.mockReturnValue({ isError: true, error, refetch });
    renderWithProviders(<CatalogTree onSelect={() => {}} onPreview={() => {}} onInsertSelect={() => {}} />);

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Could not load the catalog");
    expect(alert).toHaveTextContent("the dbt artifacts are missing; run make build-prod");
    expect(alert).toHaveTextContent("catalog_unavailable");
    await user.click(screen.getByRole("button", { name: "Retry" }));

    expect(refetch).toHaveBeenCalledOnce();
  });

  it("a catalog request that got no answer says the server is unreachable", () => {
    useCatalog.mockReturnValue({ isError: true, error: new TypeError("Failed to fetch"), refetch: () => {} });
    renderWithProviders(<CatalogTree onSelect={() => {}} onPreview={() => {}} onInsertSelect={() => {}} />);

    expect(screen.getByRole("alert")).toHaveTextContent("Could not reach the server.");
    expect(screen.getByRole("alert")).not.toHaveTextContent("Failed to fetch");
  });

  it("groups by domain and shows badges", async () => {
    const user = userEvent.setup();
    renderWithProviders(<CatalogTree onSelect={() => {}} onPreview={() => {}} onInsertSelect={() => {}} />);

    expect(screen.getByText("sales")).toBeInTheDocument();
    expect(screen.getByText("product")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "fct_orders" }));

    expect(screen.getByText("PK")).toBeInTheDocument();
    expect(screen.getByText("PII")).toBeInTheDocument();
    expect(screen.getByText("bigint")).toBeInTheDocument();
  });

  it("the chevron opens and closes a table's columns without selecting it; the name does both", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    renderWithProviders(<CatalogTree onSelect={onSelect} onPreview={() => {}} onInsertSelect={() => {}} />);
    const name = screen.getByRole("button", { name: "fct_orders" });

    await user.click(screen.getByRole("button", { name: "Show the columns of fct_orders" }));
    expect(name).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("order_id")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Hide the columns of fct_orders" }));
    expect(screen.queryByText("order_id")).not.toBeInTheDocument();
    expect(onSelect).not.toHaveBeenCalled();

    await user.click(name);

    expect(onSelect).toHaveBeenCalledWith("fct_orders");
    expect(name).toHaveAttribute("aria-expanded", "true");
  });

  it("search keeps parents of matching columns", async () => {
    const user = userEvent.setup();
    renderWithProviders(<CatalogTree onSelect={() => {}} onPreview={() => {}} onInsertSelect={() => {}} />);

    await user.type(screen.getByLabelText("Search tables and columns"), "EMAIL");

    expect(screen.getByText("sales")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "fct_orders" })).toBeInTheDocument();
    expect(screen.getByText("customer_email")).toBeInTheDocument();
    expect(screen.queryByText("order_id")).not.toBeInTheDocument();
    expect(screen.queryByText("product")).not.toBeInTheDocument();
  });

  it("a search that matches nothing says so, and the clear button brings every table back", async () => {
    const user = userEvent.setup();
    renderWithProviders(<CatalogTree onSelect={() => {}} onPreview={() => {}} onInsertSelect={() => {}} />);
    expect(screen.queryByRole("button", { name: "Clear search" })).not.toBeInTheDocument();

    await user.type(screen.getByLabelText("Search tables and columns"), " zzz ");
    expect(screen.getByText("No table or column matches “zzz”.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "fct_orders" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Clear search" }));

    expect(screen.getByLabelText("Search tables and columns")).toHaveValue("");
    expect(screen.getByLabelText("Search tables and columns")).toHaveFocus();
    expect(screen.getByRole("button", { name: "fct_orders" })).toBeInTheDocument();
    expect(screen.queryByText(/No table or column matches/)).not.toBeInTheDocument();
  });

  it("hover shows the description's first sentence, the grain and the PK", async () => {
    const user = userEvent.setup();
    const description = "One row per order.\nCancelled orders are kept and flagged. Metrics filter on them.";
    useCatalog.mockReturnValue({ data: withOrders({ description }) });
    renderWithProviders(<CatalogTree onSelect={() => {}} onPreview={() => {}} onInsertSelect={() => {}} />);

    await user.hover(screen.getByRole("button", { name: "fct_orders" }));

    const tip = await screen.findByRole("tooltip");
    expect(tip).toHaveTextContent("One row per order.Grain: one row per orderPK: order_id");
    expect(tip).not.toHaveTextContent("Cancelled");
  });

  it("hover cuts a long first sentence at 200 characters", async () => {
    const user = userEvent.setup();
    useCatalog.mockReturnValue({ data: withOrders({ description: `${"word ".repeat(60)}end. Next.` }) });
    renderWithProviders(<CatalogTree onSelect={() => {}} onPreview={() => {}} onInsertSelect={() => {}} />);

    await user.hover(screen.getByRole("button", { name: "fct_orders" }));

    const summary = (await screen.findByRole("tooltip")).firstElementChild?.textContent ?? "";
    expect(summary).toHaveLength(200);
    expect(summary.endsWith("…")).toBe(true);
  });

  it("the tooltip closes when the table menu opens", async () => {
    const user = userEvent.setup();
    renderWithProviders(<CatalogTree onSelect={() => {}} onPreview={() => {}} onInsertSelect={() => {}} />);
    const name = screen.getByRole("button", { name: "fct_orders" });
    await user.hover(name);
    await screen.findByRole("tooltip");

    await user.pointer({ keys: "[MouseRight]", target: name });

    expect(await screen.findByRole("menu")).toBeInTheDocument();
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("menu opens from right-click and runs the editor action", async () => {
    const user = userEvent.setup();
    const onPreview = vi.fn();
    renderWithProviders(<CatalogTree onSelect={() => {}} onPreview={onPreview} onInsertSelect={() => {}} />);

    await user.pointer({ keys: "[MouseRight]", target: screen.getByRole("button", { name: "fct_orders" }) });
    await user.click(await screen.findByRole("menuitem", { name: "Preview 100 rows" }));

    expect(onPreview).toHaveBeenCalledWith("marts.fct_orders");
  });

  it("a blocked preview is disabled in the menu and says why", async () => {
    const user = userEvent.setup();
    const onPreview = vi.fn();
    renderWithProviders(
      <CatalogTree onSelect={() => {}} onPreview={onPreview} onInsertSelect={() => {}} previewBlocked="Database unavailable" />,
    );

    await user.click(screen.getByRole("button", { name: "Actions for dim_products" }));

    const preview = await screen.findByRole("menuitem", { name: "Preview 100 rows" });
    expect(preview).toBeDisabled();
    expect(preview).toHaveAccessibleDescription("Database unavailable");
    expect(screen.getByRole("menuitem", { name: "Insert SELECT" })).toBeEnabled();
  });

  it("Insert SELECT in the ⋯ menu reports the qualified name", async () => {
    const user = userEvent.setup();
    const onInsertSelect = vi.fn();
    renderWithProviders(<CatalogTree onSelect={() => {}} onPreview={() => {}} onInsertSelect={onInsertSelect} />);

    await user.click(screen.getByRole("button", { name: "Actions for dim_products" }));
    await user.click(await screen.findByRole("menuitem", { name: "Insert SELECT" }));

    expect(onInsertSelect).toHaveBeenCalledWith("marts.dim_products");
  });

  it("Open in Lineage links to the table path", async () => {
    const user = userEvent.setup();
    renderWithProviders(<CatalogTree onSelect={() => {}} onPreview={() => {}} onInsertSelect={() => {}} />);

    await user.click(screen.getByRole("button", { name: "Actions for dim_products" }));

    const menu = await screen.findByRole("menu");
    expect(within(menu).getByRole("menuitem", { name: "Open in Lineage" })).toHaveAttribute(
      "href",
      "/lineage?select=%2Bdim_products%2B",
    );
    expect(within(menu).getByRole("menuitem", { name: "Copy name" })).toBeEnabled();
  });
});

describe("Copy name", () => {
  it("copies the table's qualified name", async () => {
    const user = userEvent.setup();
    renderWithProviders(<CatalogTree onSelect={() => {}} onPreview={() => {}} onInsertSelect={() => {}} />);

    await user.click(screen.getByRole("button", { name: "Actions for dim_products" }));
    await user.click(await screen.findByRole("menuitem", { name: "Copy name" }));

    expect(await navigator.clipboard.readText()).toBe("marts.dim_products");
  });
});

describe("Inspector", () => {
  it("a table that fails to load shows the API's message and code, and Retry fetches it again", async () => {
    const user = userEvent.setup();
    const refetch = vi.fn();
    const error = apiError("table_not_found", "no mart named fct_orders");
    useTableDetails.mockReset().mockReturnValue({ isError: true, error, refetch });
    renderWithProviders(<Inspector table="fct_orders" />);

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Could not load fct_orders");
    expect(alert).toHaveTextContent("no mart named fct_orders");
    expect(alert).toHaveTextContent("table_not_found");
    await user.click(screen.getByRole("button", { name: "Retry" }));

    expect(refetch).toHaveBeenCalledOnce();
  });

  it("shows accepted values, synonyms, metric definitions and tests", () => {
    useTableDetails.mockReset().mockReturnValue({ data: DETAILS });
    renderWithProviders(<Inspector table="fct_orders" />);

    expect(screen.getByText(/Accepted values: placed, shipped/)).toBeInTheDocument();
    expect(screen.getByText(/Synonyms: state/)).toBeInTheDocument();
    expect(screen.getByText(/Revenue after refunds\./)).toBeInTheDocument();
    expect(screen.getByText("unique_fct_orders_order_id")).toBeInTheDocument();
  });

  it("Open in Lineage links to the table path", () => {
    useTableDetails.mockReset().mockReturnValue({ data: DETAILS });
    renderWithProviders(<Inspector table="fct_orders" />);

    expect(screen.getByRole("link", { name: "Open in Lineage" })).toHaveAttribute(
      "href",
      "/lineage?select=%2Bfct_orders%2B",
    );
  });
});
