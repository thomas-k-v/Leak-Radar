import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { parseSseChunk } from "../api/sse";
import { fmtInrShort, indianGroup } from "../format";
import { ComponentSchema } from "./components";

const FIXTURES = join(__dirname, "../../../tests/fixtures/components");
const load = (kind: string) =>
  readdirSync(join(FIXTURES, kind))
    .filter((f) => f.endsWith(".json"))
    .map((f) => [f, JSON.parse(readFileSync(join(FIXTURES, kind, f), "utf8"))] as const);

describe("component schemas agree with the backend fixtures", () => {
  it.each(load("valid"))("accepts %s", (_name, spec) => {
    expect(ComponentSchema.safeParse(spec).success).toBe(true);
  });
  it.each(load("invalid"))("rejects %s", (_name, spec) => {
    expect(ComponentSchema.safeParse(spec).success).toBe(false);
  });
});

describe("sse parser", () => {
  it("splits complete events and keeps the remainder", () => {
    const { events, rest } = parseSseChunk('data: {"type":"text","text":"hi"}\n\ndata: {"type":"st');
    expect(events).toEqual([{ type: "text", text: "hi" }]);
    expect(rest).toBe('data: {"type":"st');
  });
});

describe("formatting matches the backend", () => {
  it("uses Indian grouping and lakh/crore short forms", () => {
    expect(indianGroup(13842000)).toBe("1,38,42,000");
    expect(fmtInrShort(138420)).toBe("₹1.38L");
    expect(fmtInrShort(21500000)).toBe("₹2.15Cr");
    expect(fmtInrShort(48250)).toBe("₹48,250");
  });
});
