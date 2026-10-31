import type { components } from "../../src/api/schema";

// Builders for the API's payloads. Each returns the full generated schema type from complete defaults, so a field the
// API adds or requires breaks `npm run typecheck` here, in one place, instead of going stale in a test's literal.
type Schemas = components["schemas"];

export type QueryResponse = Schemas["QueryResponse"];
export type CatalogTree = Schemas["CatalogTree"];
export type TableSummary = Schemas["TableSummary"];
export type ColumnSummary = Schemas["ColumnSummary"];
export type TableDetails = Schemas["TableDetails"];
export type ColumnDetails = Schemas["ColumnDetails"];
export type LineageNode = Schemas["LineageNode"];
export type LineageGraph = Schemas["LineageGraph"];
export type NodeDetails = Schemas["NodeDetails"];
export type Health = Schemas["Health"];
export type ApiError = Schemas["ApiError"];
export type FormatResponse = Schemas["FormatResponse"];

/** What openapi-fetch resolves with for a 2xx answer. */
export type OkResult<T> = { data: T; response: { status: number } };
/** What it resolves with for an API error answer. */
export type FailureResult = { error: ApiError; response: { status: number } };

export function queryResponse(overrides: Partial<QueryResponse> = {}): QueryResponse {
  return {
    query_id: "q",
    columns: ["n"],
    column_types: ["number"],
    rows: [["1"], ["2"]],
    returned_rows: 2,
    truncated: false,
    elapsed_ms: 5,
    mode: "client",
    ...overrides,
  };
}

export function formatResponse(overrides: Partial<FormatResponse> = {}): FormatResponse {
  return { sql: "SELECT 1", ...overrides };
}

export function catalog(overrides: Partial<CatalogTree> = {}): CatalogTree {
  return { database: "sherpa", schema_name: "marts", domains: [], ...overrides };
}

export function table(overrides: Partial<TableSummary> = {}): TableSummary {
  return {
    name: "fct_orders",
    domain: "sales",
    description: "",
    grain: "",
    primary_key: [],
    columns: [],
    ...overrides,
  };
}

export function columnSummary(overrides: Partial<ColumnSummary> = {}): ColumnSummary {
  return { name: "order_id", data_type: "text", description: "", is_primary_key: false, is_pii: false, ...overrides };
}

export function tableDetails(overrides: Partial<TableDetails> = {}): TableDetails {
  return {
    name: "fct_orders",
    domain: "sales",
    description: "",
    grain: "",
    primary_key: [],
    synonyms: [],
    tests: [],
    columns: [],
    ...overrides,
  };
}

export function columnDetails(overrides: Partial<ColumnDetails> = {}): ColumnDetails {
  return {
    name: "status",
    data_type: "text",
    description: "",
    is_primary_key: false,
    is_pii: false,
    accepted_values: null,
    synonyms: [],
    metric_refs: [],
    ...overrides,
  };
}

export function lineageNode(overrides: Partial<LineageNode> = {}): LineageNode {
  return {
    unique_id: "model.sherpa.fct_orders",
    name: "fct_orders",
    resource_type: "model",
    layer: "marts",
    domain: "sales",
    materialization: "table",
    status: "ok",
    failing_tests: 0,
    ...overrides,
  };
}

export function lineageGraph(overrides: Partial<LineageGraph> = {}): LineageGraph {
  return {
    nodes: [],
    edges: [],
    layers_available: [],
    build: { state: "ok", node_count: 0, started_at: null },
    ...overrides,
  };
}

export function nodeDetails(overrides: Partial<NodeDetails> = {}): NodeDetails {
  return {
    unique_id: "model.sherpa.fct_orders",
    name: "fct_orders",
    resource_type: "model",
    layer: "marts",
    domain: "sales",
    materialization: "table",
    status: "ok",
    failing_tests: 0,
    selector: "fct_orders",
    description: "",
    file_path: "models/marts/sales/fct_orders.sql",
    is_mart: true,
    contract_enforced: true,
    build: null,
    columns: [],
    tests: [],
    raw_sql: null,
    compiled_sql: null,
    sql_note: null,
    ...overrides,
  };
}

export function health(overrides: Partial<Health> = {}): Health {
  return { database: "ok", artifacts: { status: "ok", generated_at: "2026-10-24T10:00:00Z" }, ...overrides };
}

export function ok<T>(data: T, status = 200): OkResult<T> {
  return { data, response: { status } };
}

/** An API error answer; `position` is where Postgres stopped in the SQL. */
export function apiFailure(code: string, message: string, status: number, position?: number): FailureResult {
  const error: ApiError = position === undefined ? { code, message } : { code, message, detail: { position } };
  return { error, response: { status } };
}

/** An error answer whose body is not the API's (a proxy's HTML page): the one body that is not an `ApiError`. */
export function proxyFailure(body: string, status: number): { error: string; response: { status: number } } {
  return { error: body, response: { status } };
}

/** The answer to a call that has no body (the cancel). */
export function noContent(): { response: { status: number } } {
  return { response: { status: 204 } };
}
