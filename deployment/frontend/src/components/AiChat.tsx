import { useState, useRef, useEffect, type ReactNode } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Send,
  Sparkles,
  Link2,
  AlertTriangle,
  TrendingDown,
  ShieldCheck,
  ChevronsUpDown,
  X,
  ChevronDown,
} from "lucide-react";
import { Button } from "./ui/button";
import { Label } from "./ui/label";
import { Switch } from "./ui/switch";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "./ui/collapsible";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "./ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "./ui/popover";
import { useUser } from "../context/UserContext";
import {
  fetchProducts,
  sendPredict,
  type FinancialFeaturesView,
  type PredictResponse,
  type ProductListItem,
} from "../services/api";

type Signal = "green" | "yellow" | "red";

interface AssistantLayer1 {
  leadParagraph: string;
  summaryLines: [string, string, string];
  closingLine: string;
}

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  signal?: Signal;
  layer1?: AssistantLayer1;
  predictResponse?: PredictResponse;
  nudge?: string;
}

const signalConfig: Record<Signal, { color: string; bg: string; border: string; label: string; icon: typeof ShieldCheck }> = {
  green: { color: "text-success", bg: "bg-success/10", border: "border-success/25", label: "Safe to Buy", icon: ShieldCheck },
  yellow: { color: "text-caution", bg: "bg-caution/10", border: "border-caution/25", label: "Proceed with Caution", icon: AlertTriangle },
  red: { color: "text-destructive", bg: "bg-destructive/10", border: "border-destructive/25", label: "Not Recommended", icon: TrendingDown },
};

function mapRecommendationToSignal(rec: string): Signal | undefined {
  const u = rec.toUpperCase();
  if (u === "GREEN") return "green";
  if (u === "YELLOW") return "yellow";
  if (u === "RED") return "red";
  return undefined;
}

/** Matches `financial_engine.py`: 5 RED compound rules, 5 YELLOW compound rules. */
const RED_RULE_TOTAL = 5;
const YELLOW_RULE_TOTAL = 5;

type SeverityLevel = "ok" | "warn" | "critical" | "na";

function SeverityDot({ level }: { level: SeverityLevel }) {
  const cls =
    level === "critical"
      ? "bg-destructive"
      : level === "warn"
        ? "bg-amber-500"
        : level === "ok"
          ? "bg-emerald-500/85"
          : "bg-muted-foreground/35";
  return (
    <span
      className={`inline-block h-1.5 w-1.5 shrink-0 rounded-full ${cls}`}
      title={level}
      aria-hidden
    />
  );
}

/**
 * CRI = (credit_score − 299) / 550 — see `model_pipeline/src/features/affordability.py`
 * (same formula as `credit_risk_indicator`). FICO-style UI bands (not the engine’s 0.35 CRI cut):
 * &lt;580 → red, 580–669 → yellow, ≥670 → green.
 */
const CRI_CREDIT_FAIR = (580 - 299) / 550;
const CRI_CREDIT_GOOD = (670 - 299) / 550;

/** Neutral dot — features the engine does not use with a single scalar UI band (DI, PIR, RUS, SPR, NWI, STIR). */
function severityNeutral(): SeverityLevel {
  return "na";
}

/**
 * DTI (debt-to-income): `financial_engine.py` RED-4 uses `dti > 0.30` with `efm < 1` and `pir > 0.10`.
 * UX: &lt;28% ok; 28–30% warn (approaching stress); &gt;30% critical (matches engine stress threshold).
 */
function severityDTI(v: number): SeverityLevel {
  if (v < 0.28) return "ok";
  if (v <= 0.3) return "warn";
  return "critical";
}

/**
 * MEB: YELLOW-3 / RED-5 use `meb > 0.70` (with other conditions); RED-2 uses `meb > 0.80`.
 * UI: &gt;70% critical per product spec; otherwise ok.
 */
function severityMEB(v: number): SeverityLevel {
  return v > 0.7 ? "critical" : "ok";
}

/**
 * EFM (months): RED-2/3 use `efm < 3`; RED-4 uses `efm < 1`; YELLOW-4 uses `efm < 3` with affordability &lt; 0.
 * UI bands: &lt;3 critical; 3–6 warn; &gt;6 ok.
 */
function severityEFM(v: number): SeverityLevel {
  if (v < 3) return "critical";
  if (v <= 6) return "warn";
  return "ok";
}

/**
 * AFS = discretionary_income − price (`affordability.py`). Engine treats `affordability &lt; 0` across RED/YELLOW rules.
 * Unreliable flag matches clamp path in affordability when |raw| exceeds safe bounds.
 */
function severityAFS(v: number, unreliable: boolean): SeverityLevel {
  if (unreliable) return "critical";
  if (v < 0) return "critical";
  if (v > 0) return "ok";
  return "warn";
}

/** FICO bands via inverted CRI; see CRI formula in `affordability.py`. */
function severityCRI(cri: number | null | undefined): SeverityLevel {
  if (cri == null || Number.isNaN(cri)) return "na";
  if (cri < CRI_CREDIT_FAIR) return "critical";
  if (cri < CRI_CREDIT_GOOD) return "warn";
  return "ok";
}

function fmtMoney(n: number): string {
  return `$${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}
function fmtRatio01(n: number): string {
  return `${(n * 100).toFixed(1)}%`;
}

/** STIR = liquid savings ÷ monthly income (same as pipeline/DB). Show as × income, not %. */
function fmtStirMultiple(n: number): string {
  if (!Number.isFinite(n)) return "—";
  return `${n.toFixed(2)}×`;
}
function fmtNum(n: number | null | undefined, digits = 2): string {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toFixed(digits);
}
function fmtPct01(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  return `${(n * 100).toFixed(0)}%`;
}

function resolveFinancialFeatures(res: PredictResponse): FinancialFeaturesView {
  if (res.financial_features) return res.financial_features;
  return {
    discretionary_income: null,
    debt_to_income_ratio: res.debt_to_income_ratio ?? null,
    saving_to_income_ratio: null,
    monthly_expense_burden_ratio: null,
    emergency_fund_months: res.emergency_fund_months ?? null,
    affordability_score: res.affordability_score ?? null,
    price_to_income_ratio: null,
    residual_utility_score: null,
    savings_to_price_ratio: null,
    net_worth_indicator: null,
    credit_risk_indicator: null,
  };
}

function countRulePrefix(rules: string[] | undefined, prefix: string): number {
  if (!rules?.length) return 0;
  return rules.filter((r) => r.startsWith(prefix)).length;
}

function stripCategoryBreadcrumb(s: string | null | undefined): string {
  if (!s?.trim()) return "—";
  const t = s.trim();
  if (t.includes(">")) {
    const parts = t.split(/\s*>\s*/).map((p) => p.trim()).filter(Boolean);
    return parts[parts.length - 1] ?? "—";
  }
  return t;
}

function sentimentShort(s: string | null | undefined): string {
  if (!s?.trim()) return "—";
  const t = s.trim();
  const head = t.split(/[,|]/)[0]?.trim() ?? t;
  return head.length > 96 ? `${head.slice(0, 93)}…` : head;
}

/**
 * Strip catalog noise after the first vertical bar (ASCII `|` or fullwidth `｜`).
 * Same logic as the main advisor paragraph; empty string uses `emptyFallback`.
 */
function truncateProductNameAtPipe(raw: string | null | undefined, emptyFallback: string): string {
  const trimmed = (raw ?? "").trim();
  if (!trimmed) return emptyFallback;
  const beforeBar = trimmed.split(/[|｜]/)[0]?.trim() ?? "";
  return beforeBar || emptyFallback;
}

function TechnicalFeatureRow({
  abbrev,
  fullLabel,
  value,
  severity,
}: {
  abbrev: string;
  fullLabel: string;
  value: ReactNode;
  severity: SeverityLevel;
}) {
  return (
    <div className="grid grid-cols-[4.5rem_minmax(0,1fr)_auto] items-center gap-x-2 border-b border-border/20 py-1 text-[11px] last:border-b-0">
      <span className="font-mono text-[10px] text-muted-foreground">{abbrev}</span>
      <span className="min-w-0 text-muted-foreground" title={fullLabel}>
        {fullLabel}
      </span>
      <span className="inline-flex max-w-full flex-wrap items-baseline justify-end gap-x-1.5 gap-y-0.5 text-right font-mono tabular-nums text-foreground/90">
        <SeverityDot level={severity} />
        {value}
      </span>
    </div>
  );
}

function SectionBlock({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-md border border-border/30 bg-background/40 px-2.5 py-2">
      <h4 className="mb-2 border-b border-border/25 pb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        {title}
      </h4>
      {children}
    </section>
  );
}

function Subheading({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <p className={`mb-1 text-[10px] font-medium text-muted-foreground/90 ${className ?? ""}`}>{children}</p>
  );
}

function TechnicalDetailsBody({ res }: { res: PredictResponse }) {
  const ff = resolveFinancialFeatures(res);
  const di = ff.discretionary_income ?? 0;
  const dti = ff.debt_to_income_ratio ?? 0;
  const stirRaw = ff.saving_to_income_ratio;
  const meb = ff.monthly_expense_burden_ratio ?? 0;
  const efm = ff.emergency_fund_months ?? 0;
  const afs = ff.affordability_score ?? 0;
  const pir = ff.price_to_income_ratio;
  const rus = ff.residual_utility_score;
  const spr = ff.savings_to_price_ratio;
  const nwi = ff.net_worth_indicator;
  const cri = ff.credit_risk_indicator;

  const redN = countRulePrefix(res.triggered_rules, "red:");
  const yellowN = countRulePrefix(res.triggered_rules, "yellow:");

  const l1 = (res.layer1_recommendation ?? "—").toUpperCase();
  const finalL = (res.recommendation ?? "—").toUpperCase();

  let downgradeLine = "No";
  if (res.was_downgraded) {
    const p2 = (res.layer2_product_triggers ?? []).join(", ") || "—";
    const r2 = (res.layer2_review_triggers ?? []).join(", ") || "—";
    downgradeLine = `Yes · P2: ${p2} · R2: ${r2}`;
  }

  const mode =
    res.evaluation_mode === "catalog"
      ? "catalog"
      : res.evaluation_mode === "hypothetical"
        ? "hypothetical"
        : res.evaluation_mode === "none"
          ? "none"
          : String(res.evaluation_mode ?? "—");

  const ps = res.product_signals;
  const rs = res.review_signals;

  const mlReady =
    res.confidence != null && !Number.isNaN(res.confidence) && res.ml_unavailable_reason == null;
  const modelName = res.ml_model_name ?? "SavVio classifier";

  return (
    <div className="space-y-3 pt-2">
      {/* 1 — Financial features */}
      <SectionBlock title="1 · Financial features">
        <Subheading>User profile</Subheading>
        <TechnicalFeatureRow
          abbrev="DI"
          fullLabel="Discretionary income"
          value={fmtMoney(di)}
          severity={severityNeutral()}
        />
        <TechnicalFeatureRow
          abbrev="DTI"
          fullLabel="Debt-to-income ratio"
          value={fmtRatio01(dti)}
          severity={severityDTI(dti)}
        />
        <TechnicalFeatureRow
          abbrev="STIR"
          fullLabel="Liquid savings ÷ monthly income"
          value={
            stirRaw != null && Number.isFinite(stirRaw) ? fmtStirMultiple(stirRaw) : "—"
          }
          severity={stirRaw != null && Number.isFinite(stirRaw) ? severityNeutral() : "na"}
        />
        <TechnicalFeatureRow
          abbrev="MEB"
          fullLabel="Monthly expense burden"
          value={fmtRatio01(meb)}
          severity={severityMEB(meb)}
        />
        <TechnicalFeatureRow
          abbrev="EFM"
          fullLabel="Emergency fund (months)"
          value={fmtNum(efm, 2)}
          severity={severityEFM(efm)}
        />

        <Subheading className="mt-2">Purchase pair</Subheading>
        <TechnicalFeatureRow
          abbrev="AFS"
          fullLabel="Affordability score"
          value={
            res.affordability_score_unreliable ? (
              "unreliable"
            ) : afs < 0 ? (
              <>
                <span className="text-destructive">{fmtNum(afs, 2)}</span>
                <span className="font-sans text-[9px] font-normal text-muted-foreground">
                  below affordability threshold
                </span>
              </>
            ) : (
              fmtNum(afs, 2)
            )
          }
          severity={severityAFS(afs, !!res.affordability_score_unreliable)}
        />
        <TechnicalFeatureRow
          abbrev="PIR"
          fullLabel="Price-to-income ratio"
          value={pir != null && !Number.isNaN(pir) ? fmtNum(pir, 4) : "—"}
          severity={pir != null && !Number.isNaN(pir) ? severityNeutral() : "na"}
        />
        <TechnicalFeatureRow
          abbrev="RUS"
          fullLabel="Residual utility score"
          value={rus != null && !Number.isNaN(rus) ? fmtNum(rus, 4) : "—"}
          severity={rus != null && !Number.isNaN(rus) ? severityNeutral() : "na"}
        />
        <TechnicalFeatureRow
          abbrev="SPR"
          fullLabel="Savings-to-price ratio"
          value={spr != null && !Number.isNaN(spr) ? fmtNum(spr, 2) : "—"}
          severity={spr != null && !Number.isNaN(spr) ? severityNeutral() : "na"}
        />
        <TechnicalFeatureRow
          abbrev="NWI"
          fullLabel="Net worth indicator"
          value={nwi != null && !Number.isNaN(nwi) ? fmtNum(nwi, 2) : "—"}
          severity={nwi != null && !Number.isNaN(nwi) ? severityNeutral() : "na"}
        />
        <TechnicalFeatureRow
          abbrev="CRI"
          fullLabel="Credit risk indicator"
          value={cri != null && !Number.isNaN(cri) ? fmtNum(cri, 4) : "—"}
          severity={cri != null && !Number.isNaN(cri) ? severityCRI(cri) : "na"}
        />
      </SectionBlock>

      {/* 2 — Decision engine */}
      <SectionBlock title="2 · Decision engine">
        <div className="space-y-1 text-[11px]">
          <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2 border-b border-border/15 py-1 font-mono tabular-nums">
            <span className="text-muted-foreground">Product</span>
            <span className="text-right text-foreground/90 break-words">
              {truncateProductNameAtPipe(res.product_name, "—")}
            </span>
          </div>
          <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2 border-b border-border/15 py-1 font-mono tabular-nums">
            <span className="text-muted-foreground">Price</span>
            <span className="text-right">
              {res.product_price != null && !Number.isNaN(res.product_price)
                ? `$${res.product_price.toLocaleString(undefined, { maximumFractionDigits: 2 })}`
                : "—"}
            </span>
          </div>
          <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2 border-b border-border/15 py-1 font-mono tabular-nums">
            <span className="text-muted-foreground">Evaluation mode</span>
            <span className="text-right uppercase">{mode}</span>
          </div>
          <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2 border-b border-border/15 py-1 font-mono tabular-nums">
            <span className="text-muted-foreground">RED rules fired</span>
            <span className="text-right">
              {redN} of {RED_RULE_TOTAL}
            </span>
          </div>
          <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2 border-b border-border/15 py-1 font-mono tabular-nums">
            <span className="text-muted-foreground">YELLOW rules fired</span>
            <span className="text-right">
              {yellowN} of {YELLOW_RULE_TOTAL}
            </span>
          </div>
          <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2 border-b border-border/15 py-1">
            <span className="text-muted-foreground">Layer 1 label</span>
            <span className="text-right font-mono text-foreground/90">{l1}</span>
          </div>
          <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2 border-b border-border/15 py-1">
            <span className="text-muted-foreground">Layer 2 downgrade</span>
            <span className="text-right font-mono text-[10px] leading-snug text-foreground/90 break-words">
              {downgradeLine}
            </span>
          </div>
          <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2 py-1">
            <span className="text-muted-foreground">Final label</span>
            <span className="text-right font-mono font-semibold text-foreground">{finalL}</span>
          </div>
        </div>
      </SectionBlock>

      {/* 3 — Product signals */}
      <SectionBlock title="3 · Product signals">
        <div className="space-y-1 text-[11px]">
          <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2 border-b border-border/15 py-1">
            <span className="text-muted-foreground">Rating</span>
            <span className="text-right font-mono tabular-nums">
              {ps?.average_rating != null ? ps.average_rating.toFixed(1) : "—"}
            </span>
          </div>
          <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2 border-b border-border/15 py-1">
            <span className="text-muted-foreground">Review count</span>
            <span className="text-right font-mono tabular-nums">{ps?.rating_count ?? "—"}</span>
          </div>
          <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2 border-b border-border/15 py-1">
            <span className="text-muted-foreground">Price position in category</span>
            <span className="text-right font-mono text-[10px] leading-snug break-words">
              {stripCategoryBreadcrumb(ps?.price_position_in_category)}
            </span>
          </div>
          <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2 border-b border-border/15 py-1">
            <span className="text-muted-foreground">Value density</span>
            <span className="text-right font-mono tabular-nums">{fmtNum(ps?.value_density ?? null, 4)}</span>
          </div>
          <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2 border-b border-border/15 py-1">
            <span className="text-muted-foreground">Quality risk score</span>
            <span className="text-right font-mono tabular-nums">{fmtNum(ps?.quality_risk_score ?? null, 3)}</span>
          </div>
          <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2 py-1">
            <span className="text-muted-foreground">Review coverage score</span>
            <span className="text-right font-mono tabular-nums">{fmtPct01(ps?.review_confidence ?? null)}</span>
          </div>
        </div>
      </SectionBlock>

      {/* 4 — Review signals */}
      <SectionBlock title="4 · Review signals">
        <div className="space-y-1 text-[11px]">
          <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2 border-b border-border/15 py-1">
            <span className="text-muted-foreground">Sentiment</span>
            <span className="text-right text-[10px] leading-snug break-words">{sentimentShort(rs?.sentiment_interpretation)}</span>
          </div>
          <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2 border-b border-border/15 py-1">
            <span className="text-muted-foreground">Verified purchase rate</span>
            <span className="text-right font-mono tabular-nums">{fmtPct01(rs?.verified_purchase_ratio ?? null)}</span>
          </div>
          <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2 border-b border-border/15 py-1">
            <span className="text-muted-foreground">Review depth</span>
            <span className="text-right font-mono tabular-nums">{fmtPct01(rs?.review_depth_score ?? null)}</span>
          </div>
          <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2 border-b border-border/15 py-1">
            <span className="text-muted-foreground">Reviewer diversity</span>
            <span className="text-right font-mono tabular-nums">{fmtPct01(rs?.reviewer_diversity ?? null)}</span>
          </div>
          <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2 py-1">
            <span className="text-muted-foreground">Helpfulness concentration</span>
            <span className="text-right font-mono tabular-nums">{fmtNum(rs?.helpful_concentration ?? null, 3)}</span>
          </div>
        </div>
      </SectionBlock>

      {/* 5 — ML layer */}
      <SectionBlock title="5 · ML layer">
        {mlReady ? (
          <div className="space-y-1 text-[11px]">
            <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2 border-b border-border/15 py-1">
              <span className="text-muted-foreground">Confidence</span>
              <span className="text-right font-mono tabular-nums">
                {(res.confidence! * 100).toFixed(1)}%
              </span>
            </div>
            <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2 py-1">
              <span className="text-muted-foreground">Predicted label</span>
              <span className="text-right font-mono">{(res.ml_predicted_label ?? "—").toUpperCase()}</span>
            </div>
          </div>
        ) : (
          <p className="font-mono text-[11px] text-muted-foreground/95">
            {modelName}: pending integration
          </p>
        )}
      </SectionBlock>
    </div>
  );
}

function TechnicalDetailsPanel({ res }: { res: PredictResponse }) {
  const [open, setOpen] = useState(false);
  return (
    <Collapsible open={open} onOpenChange={setOpen} className="mt-3 rounded-lg border border-border/35 bg-muted/15 px-3 py-2">
      <CollapsibleTrigger className="flex w-full items-center gap-2 text-left text-xs font-medium text-muted-foreground hover:text-foreground/75">
        <ChevronDown
          className={`h-3.5 w-3.5 shrink-0 transition-transform ${open ? "rotate-180" : ""}`}
          aria-hidden
        />
        <span>{open ? "Technical details ↑" : "Technical details ↓"}</span>
      </CollapsibleTrigger>
      <CollapsibleContent className="data-[state=closed]:animate-none">
        <TechnicalDetailsBody res={res} />
      </CollapsibleContent>
    </Collapsible>
  );
}

function InlineBold({ text }: { text: string }) {
  return (
    <>
      {text.split(/(\*\*.*?\*\*)/).map((part, i) =>
        part.startsWith("**") && part.endsWith("**") ? (
          <strong key={i} className="font-semibold text-foreground">
            {part.slice(2, -2)}
          </strong>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  );
}

/** Short catalog titles for conversational copy (same pipe-truncation as Decision engine Product row). */
function displayProductNameForAdvisor(raw: string | null | undefined): string {
  return truncateProductNameAtPipe(raw, "this purchase");
}

function buildLeadParagraph(res: PredictResponse, signal: Signal): string {
  const name = displayProductNameForAdvisor(res.product_name);
  const price = res.product_price;
  const pricePart =
    price != null && !Number.isNaN(price) ? ` at **$${price.toFixed(2)}**` : "";
  const hypo =
    res.evaluation_mode === "hypothetical"
      ? " We're using **the price you stated** (no catalog match for this item)."
      : "";
  if (signal === "green") {
    return `Based on your financial profile, **${name}**${pricePart}.${hypo} This purchase looks **manageable** for your current budget.`.trim();
  }
  if (signal === "yellow") {
    return `Based on your financial profile, **${name}**${pricePart}.${hypo} This purchase is **borderline** — you may want to pause or adjust before buying.`.trim();
  }
  return `Based on your financial profile, **${name}**${pricePart}.${hypo} This purchase is **not recommended** given your current savings, debt, and cash flow.`.trim();
}

function formatEmergencySavingsLine(res: PredictResponse): string {
  const e = res.emergency_fund_months;
  if (e == null || Number.isNaN(e)) {
    return "Emergency savings: — (target: 3–6 months)";
  }
  return `Emergency savings: ${e.toFixed(1)} months (target: 3–6 months)`;
}

function formatDebtLoadLine(res: PredictResponse): string {
  const d = res.debt_to_income_ratio;
  if (d == null || Number.isNaN(d)) return "Monthly debt load: —";
  if (d <= 0.0001) return "No current debt";
  return `Monthly debt load: ${(d * 100).toFixed(1)}% of income`;
}

function formatPurchaseConfidenceLine(res: PredictResponse): string {
  const mlReady =
    res.confidence != null && !Number.isNaN(res.confidence) && res.ml_unavailable_reason == null;
  if (mlReady) {
    return `Purchase confidence: ${(res.confidence * 100).toFixed(0)}%`;
  }
  const r = res.ml_unavailable_reason;
  if (r === "scoring_error") {
    return "Purchase confidence: Unavailable (model error — see logs)";
  }
  return "Purchase confidence: Unavailable (ML layer pending)";
}

const CLOSING_BY_SIGNAL: Record<Signal, string> = {
  green: "If this remains within your monthly plan, you can proceed — still track discretionary spend.",
  yellow:
    "Consider waiting a pay cycle, comparing alternatives, or trimming other discretionary spend first.",
  red: "Prioritize essentials, debt minimums, and emergency savings before this purchase.",
};

function buildLayer1(res: PredictResponse, signal: Signal): AssistantLayer1 {
  return {
    leadParagraph: buildLeadParagraph(res, signal),
    summaryLines: [
      formatEmergencySavingsLine(res),
      formatDebtLoadLine(res),
      formatPurchaseConfidenceLine(res),
    ],
    closingLine: CLOSING_BY_SIGNAL[signal],
  };
}

const quickPrompts = [
  "Should I buy a $1,200 MacBook?",
  "Can I afford $350 headphones?",
  "Is a $2,500 Peloton worth it?",
];

export const AiChat = () => {
  const { userId } = useUser();
  const [messages, setMessages] = useState<Message[]>([
    {
      id: "welcome",
      role: "assistant",
      content:
        "Welcome to **SavVio**. I'm your AI Financial Fiduciary — I'm here to help you make purchase decisions that align with your real financial health.\n\nEnter your **User ID** in the header. Turn on **Use catalog product** to pick a real SKU from our database (enables review + quality signals), or describe a purchase in your own words (may use **stated price only** if no catalog match).",
    },
  ]);
  const [input, setInput] = useState("");
  const [isTyping, setIsTyping] = useState(false);
  const [useCatalog, setUseCatalog] = useState(false);
  const [selectedProduct, setSelectedProduct] = useState<ProductListItem | null>(null);
  const [catalogOpen, setCatalogOpen] = useState(false);
  const [catalogItems, setCatalogItems] = useState<ProductListItem[]>([]);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [searchQ, setSearchQ] = useState("");
  const searchDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!useCatalog) setSelectedProduct(null);
  }, [useCatalog]);

  useEffect(() => {
    if (!catalogOpen) return;
    if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current);
    searchDebounceRef.current = setTimeout(() => {
      setCatalogLoading(true);
      fetchProducts({ q: searchQ || undefined, limit: 100 })
        .then((r) => setCatalogItems(r.items))
        .catch(() => setCatalogItems([]))
        .finally(() => setCatalogLoading(false));
    }, searchQ.trim() ? 320 : 0);
    return () => {
      if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current);
    };
  }, [catalogOpen, searchQ]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, isTyping]);

  const handleSend = async (text?: string) => {
    const msg = text || input;
    if (!msg.trim() && !useCatalog) return;
    if (useCatalog && !selectedProduct) {
      setMessages((prev) => [
        ...prev,
        {
          id: Date.now().toString(),
          role: "assistant",
          content:
            "Select a **product from the catalog** first, or turn off **Use catalog product** to ask in free text (no SKU).",
        },
      ]);
      return;
    }
    if (!msg.trim() && useCatalog && selectedProduct) {
      void handleSend(`Should I buy ${selectedProduct.product_name}?`);
      return;
    }
    if (!msg.trim()) return;
    if (!userId.trim()) {
      setMessages((prev) => [
        ...prev,
        { id: Date.now().toString(), role: "user", content: msg },
        {
          id: (Date.now() + 1).toString(),
          role: "assistant",
          content:
            "Please set your **User ID** in the header so I can load your financial profile and run the recommendation engine.",
        },
      ]);
      setInput("");
      return;
    }

    const userMsg: Message = { id: Date.now().toString(), role: "user", content: msg };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setIsTyping(true);

    try {
      const res = await sendPredict({
        user_query: msg,
        user_id: userId.trim(),
        product_id: useCatalog && selectedProduct ? selectedProduct.product_id : undefined,
      });

      const signal = mapRecommendationToSignal(res.recommendation);

      const nudge =
        res.was_downgraded
          ? "Product and review signals caused a one-step downgrade from the pure financial assessment."
          : undefined;

      const assistantMsg: Message = {
        id: (Date.now() + 1).toString(),
        role: "assistant",
        content: res.explanation,
        signal,
        layer1: signal ? buildLayer1(res, signal) : undefined,
        predictResponse: signal ? res : undefined,
        nudge,
      };
      setMessages((prev) => [...prev, assistantMsg]);
    } catch (e) {
      const errText = e instanceof Error ? e.message : "Request failed.";
      setMessages((prev) => [
        ...prev,
        {
          id: (Date.now() + 2).toString(),
          role: "assistant",
          content: `**Something went wrong.** ${errText}\n\nMake sure the API is running on port **3500** and your user exists in the database.`,
        },
      ]);
    } finally {
      setIsTyping(false);
    }
  };

  const hasLink = (text: string) => /https?:\/\//.test(text);

  return (
    <div className="max-w-3xl mx-auto flex flex-col h-[calc(100vh-7rem)]">
      <div className="flex items-center gap-2 mb-4">
        <Sparkles className="w-5 h-5 text-primary" />
        <div>
          <h1 className="font-heading text-xl font-semibold">Purchase Advisor</h1>
          <p className="text-xs text-muted-foreground">Your fiduciary — always on your side</p>
        </div>
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto space-y-4 pb-4 pr-1 scrollbar-thin">
        {messages.map((msg) => (
          <motion.div
            key={msg.id}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[88%] rounded-2xl px-4 py-3 text-sm leading-relaxed ${
                msg.role === "user"
                  ? "bg-primary text-primary-foreground rounded-br-md"
                  : "glass-card rounded-bl-md"
              }`}
            >
              {/* Signal Badge */}
              {msg.signal &&
                (() => {
                  const cfg = signalConfig[msg.signal!];
                  const Icon = cfg.icon;
                  return (
                    <div
                      className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold mb-3 ${cfg.bg} ${cfg.color} border ${cfg.border}`}
                    >
                      <Icon className="w-3.5 h-3.5" />
                      {cfg.label}
                    </div>
                  );
                })()}

              {/* LLM explanation — shown for all responses that have content */}
              {msg.content && (
                <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground/95">
                  <InlineBold text={msg.content} />
                </p>
              )}

              {/* Quick-stats strip — Emergency fund / Debt / Confidence (structured responses only) */}
              {msg.layer1 && (
                <ul className="space-y-1 text-sm text-foreground/80 list-none pl-0 mt-2 border-t border-border/20 pt-2">
                  {msg.layer1.summaryLines.map((line, i) => (
                    <li key={i} className="leading-snug">
                      {line}
                    </li>
                  ))}
                </ul>
              )}

              {/* Link indicator */}
              {msg.role === "user" && hasLink(msg.content) && (
                <div className="flex items-center gap-1 mt-2 text-xs opacity-70">
                  <Link2 className="w-3 h-3" />
                  Product link detected
                </div>
              )}

              {/* Downgrade / quality nudge (user-facing, above technical) */}
              {msg.nudge && (
                <div className="mt-3 p-2.5 rounded-lg bg-caution/10 border border-caution/20 flex items-start gap-2">
                  <AlertTriangle className="w-3.5 h-3.5 text-caution shrink-0 mt-0.5" />
                  <p className="text-xs text-caution/90 leading-relaxed">{msg.nudge}</p>
                </div>
              )}

              {/* Layer 2 — technical / debug (collapsed by default) */}
              {msg.predictResponse && <TechnicalDetailsPanel res={msg.predictResponse} />}
            </div>
          </motion.div>
        ))}

        {/* Typing indicator */}
        <AnimatePresence>
          {isTyping && (
            <motion.div
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              className="flex justify-start"
            >
              <div className="glass-card rounded-2xl rounded-bl-md px-4 py-3 flex items-center gap-1.5">
                <div className="w-2 h-2 rounded-full bg-primary animate-pulse-glow" />
                <div className="w-2 h-2 rounded-full bg-primary animate-pulse-glow" style={{ animationDelay: "0.3s" }} />
                <div className="w-2 h-2 rounded-full bg-primary animate-pulse-glow" style={{ animationDelay: "0.6s" }} />
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* Quick Prompts */}
      {messages.length <= 1 && (
        <div className="flex flex-wrap gap-2 mb-3">
          {quickPrompts.map((prompt) => (
            <button
              key={prompt}
              onClick={() => handleSend(prompt)}
              className="text-xs px-3 py-1.5 rounded-full border border-border/60 text-muted-foreground hover:text-foreground hover:border-primary/40 transition-colors"
            >
              {prompt}
            </button>
          ))}
        </div>
      )}

      {/* Catalog product picker */}
      <div className="glass-card px-3 py-2.5 mb-2 space-y-2 rounded-xl border border-border/40">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Switch
              id="use-catalog"
              checked={useCatalog}
              onCheckedChange={(v) => setUseCatalog(!!v)}
              disabled={isTyping}
            />
            <Label htmlFor="use-catalog" className="text-sm font-medium cursor-pointer">
              Use catalog product
            </Label>
          </div>
        </div>
        {useCatalog && (
          <div className="flex flex-wrap items-center gap-2 pt-1">
            <Popover
              open={catalogOpen}
              onOpenChange={(o) => {
                setCatalogOpen(o);
                if (o) setSearchQ("");
              }}
            >
              <PopoverTrigger asChild>
                <Button
                  type="button"
                  variant="outline"
                  role="combobox"
                  aria-expanded={catalogOpen}
                  className="min-w-[200px] max-w-full justify-between font-normal text-left h-9"
                  disabled={isTyping}
                >
                  <span className="truncate">
                    {selectedProduct
                      ? `${selectedProduct.product_name.slice(0, 48)}${selectedProduct.product_name.length > 48 ? "…" : ""}`
                      : "Search products…"}
                  </span>
                  <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
                </Button>
              </PopoverTrigger>
              <PopoverContent className="w-[min(96vw,28rem)] p-0" align="start">
                <Command shouldFilter={false}>
                  <CommandInput placeholder="Search by name…" value={searchQ} onValueChange={setSearchQ} />
                  <CommandList>
                    {catalogLoading && (
                      <div className="py-6 text-center text-sm text-muted-foreground">Loading…</div>
                    )}
                    {!catalogLoading && catalogItems.length === 0 && <CommandEmpty>No products found.</CommandEmpty>}
                    {!catalogLoading && catalogItems.length > 0 && (
                      <CommandGroup>
                        {catalogItems.map((p) => (
                          <CommandItem
                            key={p.product_id}
                            value={p.product_id}
                            onSelect={() => {
                              setSelectedProduct(p);
                              setCatalogOpen(false);
                            }}
                          >
                            <span className="flex flex-col gap-0.5 min-w-0">
                              <span className="truncate font-medium">{p.product_name}</span>
                              <span className="text-xs text-muted-foreground">
                                {p.price != null ? `$${p.price.toLocaleString()}` : "—"} · {p.product_id}
                              </span>
                            </span>
                          </CommandItem>
                        ))}
                      </CommandGroup>
                    )}
                  </CommandList>
                </Command>
              </PopoverContent>
            </Popover>
            {selectedProduct && (
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="h-9 w-9 shrink-0"
                onClick={() => setSelectedProduct(null)}
                aria-label="Clear product"
              >
                <X className="h-4 w-4" />
              </Button>
            )}
            <p className="text-[11px] text-muted-foreground w-full basis-full">
              Default price band comes from the API (see <code className="text-[10px]">PRODUCT_BROWSE_PRICE_*</code>).
            </p>
          </div>
        )}
      </div>

      {/* Input */}
      <div className="glass-card p-2 flex items-center gap-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) =>
            e.key === "Enter" &&
            !e.shiftKey &&
            (input.trim() || (useCatalog && selectedProduct)) &&
            void handleSend()
          }
          placeholder="Paste a product link or ask 'Should I buy...?'"
          className="flex-1 bg-transparent px-3 py-2.5 text-sm placeholder:text-muted-foreground focus:outline-none"
          disabled={isTyping}
        />
        <Button
          size="icon"
          onClick={() => void handleSend()}
          disabled={isTyping || (!input.trim() && !(useCatalog && selectedProduct))}
          className="shrink-0 rounded-lg"
        >
          <Send className="w-4 h-4" />
        </Button>
      </div>
    </div>
  );
};
