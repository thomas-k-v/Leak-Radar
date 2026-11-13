// Mirror of leakradar/agent/components.py. Props are IDs only; anything else is rejected.
// Both are checked against the shared fixtures in tests/fixtures/components/.
import { z } from "zod";

const LeakId = z.string().regex(/^L\d{4}$/);
const PitchId = z.string().regex(/^P\d{4}$/);
const Metric = z.enum(["failure_rate", "collect_share", "recurring_fail_rate"]);

export const ComponentSchema = z.discriminatedUnion("type", [
  z.object({ type: z.literal("MerchantLeakTable"), props: z.object({ leak_ids: z.array(LeakId).min(1).max(25) }).strict() }).strict(),
  z.object({ type: z.literal("LeakCard"), props: z.object({ leak_id: LeakId }).strict() }).strict(),
  z.object({ type: z.literal("RootCauseCard"), props: z.object({ leak_id: LeakId }).strict() }).strict(),
  z.object({ type: z.literal("TrendChart"), props: z.object({ leak_id: LeakId, metric: Metric }).strict() }).strict(),
  z.object({ type: z.literal("DraftMessage"), props: z.object({ pitch_id: PitchId }).strict() }).strict(),
  z.object({ type: z.literal("EvidenceDrawer"), props: z.object({ leak_id: LeakId }).strict() }).strict(),
]);

export type ComponentSpec = z.infer<typeof ComponentSchema>;
export type ComponentType = ComponentSpec["type"];
