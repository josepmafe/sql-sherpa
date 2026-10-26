import { MantineProvider } from "@mantine/core";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CatalogTree } from "../src/screens/explorer/CatalogTree";
import { Inspector } from "../src/screens/explorer/Inspector";

const CATALOG = {
  database: "sherpa",
  schema_name: "marts",
  domains: [
    {
      name: "sales",
      tables: [
        {
          name: "fct_orders",
          domain: "sales",
          description: "One row per order.",
          grain: "one row per order",
          primary_key: ["order_id"],
          columns: [
            { name: "order_id", data_type: "bigint", description: "Order key.", is_primary_key: true, is_pii: false },
            { name: "customer_email", data_type: "text", description: "Email.", is_primary_key: false, is_pii: true },
          ],
        },
      ],
    },
    {
      name: "product",
      tables: [
        {
          name: "dim_products",
          domain: "product",
          description: "One row per product.",
          grain: "one row per product",
          primary_key: ["product_id"],
          columns: [
            { name: "sku", data_type: "text", description: "Stock keeping unit.", is_primary_key: false, is_pii: false },
          ],
        },
      ],
    },
  ],
};

// The catalog with fct_orders changed.
function withOrders(change: object) {
  const [sales, ...others] = CATALOG.domains;
  const [orders, ...rest] = sales!.tables;
  return { ...CATALOG, domains: [{ ...sales!, tables: [{ ...orders!, ...change }, ...rest] }, ...others] };
}

const DETAILS = {
  name: "fct_orders",
  domain: "sales",
  description: "One row per order.",
  grain: "one row per order",
  primary_key: ["order_id"],
  synonyms: ["purchases"],
  tests: ["unique_fct_orders_order_id"],
  columns: [
    {
      name: "status",
      data_type: "text",
      description: "Order status.",
      is_primary_key: false,
      is_pii: false,
      accepted_values: ["placed", "shipped"],
      synonyms: ["state"],
      metric_refs: [{ name: "net_revenue", definition: "Revenue after refunds." }],
    },
  ],
};

const useCatalog = vi.fn();
const useTableDetails = vi.fn();
vi.mock("../src/api/queries", () => ({
  useCatalog: () => useCatalog(),
  useTableDetails: (name: string | undefined) => useTableDetails(name),
}));

function renderWithMantine(ui: ReactNode) {
  return render(<MantineProvider env="test">{ui}</MantineProvider>);
}

describe("CatalogTree", () => {
  beforeEach(() => {
    useCatalog.mockReset().mockReturnValue({ data: CATALOG });
  });

  it("groups by domain and shows badges", async () => {
    const user = userEvent.setup();
    renderWithMantine(<CatalogTree onSelect={() => {}} onPreview={() => {}} onInsertSelect={() => {}} />);

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
    renderWithMantine(<CatalogTree onSelect={onSelect} onPreview={() => {}} onInsertSelect={() => {}} />);
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
    renderWithMantine(<CatalogTree onSelect={() => {}} onPreview={() => {}} onInsertSelect={() => {}} />);

    await user.type(screen.getByLabelText("Search tables and columns"), "EMAIL");

    expect(screen.getByText("sales")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "fct_orders" })).toBeInTheDocument();
    expect(screen.getByText("customer_email")).toBeInTheDocument();
    expect(screen.queryByText("order_id")).not.toBeInTheDocument();
    expect(screen.queryByText("product")).not.toBeInTheDocument();
  });

  it("hover shows the description's first sentence, the grain and the PK", async () => {
    const user = userEvent.setup();
    const description = "One row per order.\nCancelled orders are kept and flagged. Metrics filter on them.";
    useCatalog.mockReturnValue({ data: withOrders({ description }) });
    renderWithMantine(<CatalogTree onSelect={() => {}} onPreview={() => {}} onInsertSelect={() => {}} />);

    await user.hover(screen.getByRole("button", { name: "fct_orders" }));

    const tip = await screen.findByRole("tooltip");
    expect(tip).toHaveTextContent("One row per order.Grain: one row per orderPK: order_id");
    expect(tip).not.toHaveTextContent("Cancelled");
  });

  it("hover cuts a long first sentence at 200 characters", async () => {
    const user = userEvent.setup();
    useCatalog.mockReturnValue({ data: withOrders({ description: `${"word ".repeat(60)}end. Next.` }) });
    renderWithMantine(<CatalogTree onSelect={() => {}} onPreview={() => {}} onInsertSelect={() => {}} />);

    await user.hover(screen.getByRole("button", { name: "fct_orders" }));

    const summary = (await screen.findByRole("tooltip")).firstElementChild?.textContent ?? "";
    expect(summary).toHaveLength(200);
    expect(summary.endsWith("…")).toBe(true);
  });

  it("the tooltip closes when the table menu opens", async () => {
    const user = userEvent.setup();
    renderWithMantine(<CatalogTree onSelect={() => {}} onPreview={() => {}} onInsertSelect={() => {}} />);
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
    renderWithMantine(<CatalogTree onSelect={() => {}} onPreview={onPreview} onInsertSelect={() => {}} />);

    await user.pointer({ keys: "[MouseRight]", target: screen.getByRole("button", { name: "fct_orders" }) });
    await user.click(await screen.findByRole("menuitem", { name: "Preview 100 rows" }));

    expect(onPreview).toHaveBeenCalledWith("marts.fct_orders");
  });

  it("a blocked preview is disabled in the menu and says why", async () => {
    const user = userEvent.setup();
    const onPreview = vi.fn();
    renderWithMantine(
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
    renderWithMantine(<CatalogTree onSelect={() => {}} onPreview={() => {}} onInsertSelect={onInsertSelect} />);

    await user.click(screen.getByRole("button", { name: "Actions for dim_products" }));
    await user.click(await screen.findByRole("menuitem", { name: "Insert SELECT" }));

    expect(onInsertSelect).toHaveBeenCalledWith("marts.dim_products");
  });

  it("menu opens from the ⋯ button, with Lineage disabled and explained", async () => {
    const user = userEvent.setup();
    renderWithMantine(<CatalogTree onSelect={() => {}} onPreview={() => {}} onInsertSelect={() => {}} />);

    await user.click(screen.getByRole("button", { name: "Actions for dim_products" }));

    const menu = await screen.findByRole("menu");
    const lineage = within(menu).getByRole("menuitem", { name: "Open in Lineage" });
    expect(lineage).toBeDisabled();
    expect(lineage).toHaveAccessibleDescription("Lineage is not available yet.");
    expect(within(menu).getByText("Lineage is not available yet.")).toBeVisible();
    expect(within(menu).getByRole("menuitem", { name: "Copy name" })).toBeEnabled();
  });
});

describe("Inspector", () => {
  it("shows accepted values, synonyms, metric definitions and tests", () => {
    useTableDetails.mockReset().mockReturnValue({ data: DETAILS });
    renderWithMantine(<Inspector table="fct_orders" />);

    expect(screen.getByText(/Accepted values: placed, shipped/)).toBeInTheDocument();
    expect(screen.getByText(/Synonyms: state/)).toBeInTheDocument();
    expect(screen.getByText(/Revenue after refunds\./)).toBeInTheDocument();
    expect(screen.getByText("unique_fct_orders_order_id")).toBeInTheDocument();
    const lineage = screen.getByRole("button", { name: "Open in Lineage" });
    expect(lineage).toBeDisabled();
    expect(lineage).toHaveAccessibleDescription("Lineage is not available yet.");
    expect(screen.getByText("Lineage is not available yet.")).toBeVisible();
  });
});
