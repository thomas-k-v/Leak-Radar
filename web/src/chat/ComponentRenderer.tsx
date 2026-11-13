import { DraftMessage } from "../components/DraftMessage";
import { EvidenceDrawer } from "../components/EvidenceDrawer";
import { LeakCard } from "../components/LeakCard";
import { MerchantLeakTable } from "../components/MerchantLeakTable";
import { RootCauseCard } from "../components/RootCauseCard";
import { TrendChart } from "../components/TrendChart";
import { ComponentSchema, type ComponentSpec } from "../schemas/components";

/** Validates again on the client (defence in depth) and renders one of the six registered components. */
export function ComponentRenderer({ raw, onAsk }: { raw: { component: string; props: unknown }; onAsk: (m: string) => void }) {
  const parsed = ComponentSchema.safeParse({ type: raw.component, props: raw.props });
  if (!parsed.success) {
    return <div className="msg-error">Rejected an invalid {raw.component} component.</div>;
  }
  return <Render spec={parsed.data} onAsk={onAsk} />;
}

function Render({ spec, onAsk }: { spec: ComponentSpec; onAsk: (m: string) => void }) {
  switch (spec.type) {
    case "MerchantLeakTable":
      return <MerchantLeakTable leak_ids={spec.props.leak_ids} onAsk={onAsk} />;
    case "LeakCard":
      return <LeakCard leak_id={spec.props.leak_id} />;
    case "RootCauseCard":
      return <RootCauseCard leak_id={spec.props.leak_id} />;
    case "TrendChart":
      return <TrendChart leak_id={spec.props.leak_id} metric={spec.props.metric} />;
    case "DraftMessage":
      return <DraftMessage pitch_id={spec.props.pitch_id} />;
    case "EvidenceDrawer":
      return <EvidenceDrawer leak_id={spec.props.leak_id} />;
  }
}
