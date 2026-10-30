import { describe, expect, it } from "vitest";

import { apiError } from "../../src/api/client";
import { apiFailure, type NodeDetails, nodeDetails, proxyFailure } from "./api";

describe("the API builders", () => {
  it("a payload missing a required field fails the type check", () => {
    const withoutSelector: Omit<NodeDetails, "selector"> = nodeDetails();
    const accept = (node: NodeDetails): NodeDetails => node;

    // @ts-expect-error `selector` is required: this line must stay a compile error (npm run typecheck guards it)
    accept(withoutSelector);

    expect(accept(nodeDetails()).selector).toBe("fct_orders");
  });

  it("an error answer is one the client turns into the same error, with or without a position", () => {
    const withPosition = apiFailure("sql_error", "syntax error", 422, 7);
    const without = apiFailure("timeout", "Query exceeded 30 s.", 504);

    expect(apiError(withPosition.error, 422)).toEqual({ ...withPosition.error, detail: { position: 7 } });
    expect(apiError(without.error, 504)).toMatchObject({ code: "timeout", message: "Query exceeded 30 s." });
    expect(apiError(proxyFailure("<html>", 502).error, 502).code).toBe("http_error");
  });
});
