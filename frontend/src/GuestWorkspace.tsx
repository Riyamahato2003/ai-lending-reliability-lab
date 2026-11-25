import { useMemo, useState } from "react";
import {
  Activity, ArrowRight, Bot, CheckCircle2, ChevronRight, CircleAlert,
  FlaskConical, Gauge, GitBranch, Layers3, LayoutDashboard, Menu, Plus,
  RefreshCcw, ShieldCheck, WalletCards, X,
} from "lucide-react";

type Tab = "overview" | "applications" | "assistant" | "laboratory";
type Stage = "draft" | "compared" | "approved" | "submission_unknown" | "submitted";
type Failure = "normal" | "lost" | "duplicate";
type Application = {
  id: string; amount: number; income: number; debt: number; months: number;
  stage: Stage; lender?: string; issue?: Failure; events: string[];
};
type Offer = { name: string; apr: number; fee: number; id: string };
const OFFERS: Offer[] = [
  { name: "Northstar Finance", apr: 12.4, fee: 299, id: "LENDER-N" },
  { name: "Horizon Credit", apr: 14.1, fee: 199, id: "LENDER-H" },
  { name: "Cedar Lending", apr: 15.3, fee: 0, id: "LENDER-C" },
];
const labels: { id: Tab; label: string; sub: string; icon: typeof Activity }[] = [
  { id: "overview", label: "Overview", sub: "Workspace at a glance", icon: LayoutDashboard },
  { id: "applications", label: "Applications", sub: "Synthetic loan lifecycle", icon: WalletCards },
  { id: "assistant", label: "AI Intelligence", sub: "Evidence-linked explanations", icon: Bot },
  { id: "laboratory", label: "Reliability Lab", sub: "Failure injection", icon: FlaskConical },
];
const rupees = (n: number) => new Intl.NumberFormat("en-IN", {
  style: "currency", currency: "INR", maximumFractionDigits: 0,
}).format(n);
const slug = (n: number) => "DEMO-" + String(n).padStart(3, "0");
function Badge({ stage }: { stage: Stage }) {
  const nice: Record<Stage,string> = { draft: "Draft", compared: "Offers compared",
    approved: "Approved to submit", submission_unknown: "Confirmation unknown", submitted: "Submitted" };
  return <span className={"guest-stage " + (stage === "submitted" ? "good" :
    stage === "submission_unknown" ? "warn" : "")}>{nice[stage]}</span>;
}
function Action({ children, onClick, secondary = false, disabled = false }: {
  children: React.ReactNode; onClick: () => void; secondary?: boolean; disabled?: boolean;
}) {
  return <button type="button" disabled={disabled} onClick={onClick}
    className={"guest-button " + (secondary ? "secondary" : "")}>{children}</button>;
}
function OfferCard({ offer, selected, onSelect }: {
  offer: Offer; selected: boolean; onSelect: () => void;
}) {
  return <button className={"guest-offer " + (selected ? "selected" : "")}
    type="button" onClick={onSelect} aria-pressed={selected}>
    <div className="guest-offer-top"><strong>{offer.name}</strong>
      <span className="guest-offer-apr">{offer.apr.toFixed(1)}% APR</span></div>
    <span>Illustrative processing fee {rupees(offer.fee)}</span>
    <small>{offer.id} · synthetic terms only</small>
    <span className="guest-offer-choice">{selected ? "✓ Selected" : "Compare this offer"}</span>
  </button>;
}
function Timeline({ events }: { events: string[] }) {
  return <ol className="guest-timeline">{events.map((event, index) =>
    <li key={index}>{event}</li>)}</ol>;
}
export default function GuestWorkspace() {
  const [tab, setTab] = useState<Tab>("overview");
  const [menu, setMenu] = useState(false);
  const [applications, setApplications] = useState<Application[]>([]);
  const [chosen, setChosen] = useState<string | null>(null);
  const [amount, setAmount] = useState("24000");
  const [income, setIncome] = useState("4200");
  const [debt, setDebt] = useState("800");
  const [months, setMonths] = useState("24");
  const [selectedOffer, setSelectedOffer] = useState("LENDER-N");
  const [failure, setFailure] = useState<Failure>("lost");
  const [scenario, setScenario] = useState<Failure>("lost");
  const [runs, setRuns] = useState<{ scenario: Failure; safe: boolean; outcome: string }[]>([]);
  const [question, setQuestion] = useState("Why must we reconcile a missing confirmation?");
  const [answer, setAnswer] = useState("");
  const [notice, setNotice] = useState("");
  const current = applications.find((app) => app.id === chosen);
  const total = applications.length;
  const submitted = applications.filter((app) => app.stage === "submitted").length;
  const pending = applications.filter((app) => app.stage === "submission_unknown").length;
  const compareable = current?.stage === "compared" || current?.stage === "approved";
  const summaries = useMemo(() => [
    { label: "Applications created", value: total, icon: WalletCards, detail: "This browser session" },
    { label: "Ready to compare", value: applications.filter(a => a.stage === "draft").length, icon: Activity, detail: "Synthetic evaluations" },
    { label: "Submitted", value: submitted, icon: ShieldCheck, detail: "Verified simulated effects" },
    { label: "Need reconciliation", value: pending, icon: CircleAlert, detail: "Uncertain acknowledgements" },
  ], [applications, total, submitted, pending]);
  function change(next: Tab) { setTab(next); setMenu(false); setNotice(""); }
  function update(id: string, patch: Partial<Application>, event: string) {
    setApplications(items => items.map(app => app.id !== id ? app :
      { ...app, ...patch, events: [...app.events, event] }));
  }
  function create() {
    const a = Number(amount), i = Number(income), d = Number(debt), m = Number(months);
    if (!Number.isFinite(a) || !Number.isFinite(i) || !Number.isFinite(d) ||
        !Number.isFinite(m) || a < 1000 || a > 1000000 || i < 1 || i > 10000000 ||
        d < 0 || d > i || m < 6 || m > 120 || !Number.isInteger(m)) {
      setNotice("Enter valid fictional amounts: request ₹1,000–₹10,00,000, positive monthly income, debt no higher than income, and 6–120 whole months.");
      return;
    }
    const id = slug(applications.length + 1);
    setApplications(items => [...items, { id, amount: a, income: i, debt: d, months: m,
      stage: "draft", events: ["Synthetic application created", "Illustrative consent acknowledged for this browser-only demonstration"] }]);
    setChosen(id); setTab("applications"); setNotice("Application created. Compare the fictional lender offers below.");
  }
  function submit() {
    if (!current || current.stage !== "approved") return;
    if (failure === "lost") {
      update(current.id, { stage: "submission_unknown", issue: failure },
        "One simulated lender effect committed; its confirmation was lost. Status is unknown until reconciliation.");
      setNotice("Confirmation lost! The simulated effect exists. Reconcile instead of blindly resubmitting.");
    } else {
      update(current.id, { stage: "submitted", issue: failure },
        failure === "duplicate" ?
          "Duplicate confirmations matched one immutable intent. Exactly one synthetic effect recorded." :
          "One synthetic lender effect confirmed against its immutable dispatch intent.");
      setNotice("Simulated submission recorded safely. No real lender was contacted.");
    }
  }
  function reconcile() {
    if (!current || current.stage !== "submission_unknown") return;
    update(current.id, { stage: "submitted" },
      "Reconciled the same idempotency key against the synthetic ledger: one effect verified, zero duplicate submissions.");
    setNotice("Reconciliation complete. The original simulated effect is verified without another submission.");
  }
  function runLab(safe: boolean) {
    const outcome = scenario === "lost" ?
      (safe ? "1 verified effect · 0 duplicates · recovery by ledger" : "2 effects · 1 duplicate from blind retry") :
      scenario === "duplicate" ?
        (safe ? "1 effect · duplicate acknowledgement ignored" : "1 effect · 2 unverified confirmations counted") :
        (safe ? "1 confirmed effect · 0 policy violations" : "1 effect · no injected fault");
    setRuns(items => [{ scenario, safe, outcome }, ...items].slice(0, 6));
  }
  function explain() {
    const q = question.toLowerCase();
    const subject = current ? current.id + " (" + current.stage.replaceAll("_", " ") + ")" : "the synthetic demo";
    const response = q.includes("consent") ?
      "Consent is recorded before evaluation in the actual guarded architecture. In this browser-only example, consent is illustrative; no personal financial data is sent to the backend." :
      q.includes("duplicate") || q.includes("retry") || q.includes("idempot") ?
      "A durable idempotency key binds every dispatch attempt to one immutable intent. A retry must reuse that key, then confirm the authoritative effect ledger. A fresh key after an ambiguous response can duplicate the effect." :
      q.includes("offer") || q.includes("rate") ?
      "Offer examples use fixed synthetic terms. Lowest example APR is Northstar Finance at 12.4%. They are not financial offers, eligibility decisions, or real-world interest-rate quotes." :
      "When the lender commits an effect but its acknowledgement is lost, the workflow must stay unresolved. Reconciliation queries the authoritative simulated ledger, verifies the original intent, and only then marks it confirmed. It must never infer a successful submission from a timeout alone.";
    setAnswer(response + "\n\nEvidence basis: " + subject + "; demo policy SAFE-IDEMPOTENCY-V1; illustrative lender ledger. No live AI model or external database was consulted.");
  }
  return <div className="app-shell guest-shell">
    <aside className={"sidebar " + (menu ? "open" : "")}>
      <div className="side-top">
        <div className="brand"><span className="brand-mark"><Layers3 size={22}/></span>
          <span>verity<span className="brand-period">.</span></span></div>
        <button className="close-mobile" aria-label="Close menu" onClick={() => setMenu(false)}><X size={20}/></button>
        <div className="workspace-label">WORKSPACE</div>
        <div className="workspace-card"><span className="workspace-icon"><ShieldCheck size={20}/></span>
          <div><strong>Reliability Lab</strong><small>Open public sandbox</small></div></div>
        <div className="navigation-label">EXPLORE</div>
        <nav aria-label="Main navigation">{labels.map(item => {
          const Icon = item.icon;
          return <button key={item.id} type="button" onClick={() => change(item.id)}
            className={"nav-item " + (tab === item.id ? "active" : "")}>
            <Icon size={19}/><span>{item.label}<small>{item.sub}</small></span>
            {tab === item.id && <span className="nav-indicator"/>}</button>;
        })}</nav>
      </div>
      <div className="side-bottom"><div className="environment"><span className="pulse-dot"/> PUBLIC DEMO READY</div>
        <div className="side-account"><div className="avatar">G</div>
          <div><strong>Guest explorer</strong><small>No login required</small></div></div></div>
    </aside>
    <div className="main-area">
      <header className="topbar">
        <button type="button" className="menu-trigger" aria-label="Open menu" onClick={() => setMenu(!menu)}><Menu size={22}/></button>
        <span className="crumb">Workspace <ChevronRight size={14}/> <b>{labels.find(l => l.id === tab)?.label}</b></span>
        <div className="top-actions"><span className="demo-chip"><span/> PUBLIC SYNTHETIC DEMO</span>
          <button type="button" className="icon-btn" aria-label="Reset guest demo" title="Reset guest demo"
            onClick={() => { setApplications([]); setChosen(null); setRuns([]); setAnswer(""); setNotice("Demo reset. All in-browser test scenarios cleared."); setTab("overview"); }}>
            <RefreshCcw size={17}/></button><div className="top-avatar">G</div></div>
      </header>
      <div className="page-content guest-main">
        <div className="guest-disclaimer"><ShieldCheck size={18}/>
          <div><strong>Public interactive sandbox — no account needed.</strong>
            All data and lender effects are simulated in this browser tab. No real lender, credit check,
            real AI call or backend workflow write. Please use invented amounts only. Reset clears your demo.</div></div>
        {notice && <div className="guest-notice" role="status"><span>{notice}</span>
          <button aria-label="Dismiss message" onClick={() => setNotice("")}><X size={15}/></button></div>}
        {tab === "overview" && <>
          <div className="page-intro"><div><span className="eyebrow">WELCOME TO VERITY</span>
            <h1>Reliability, made visible.</h1>
            <p>Explore the synthetic lending lifecycle and test how failures are recovered safely.</p></div>
            <Action onClick={() => change("applications")}>Create application <ArrowRight size={17}/></Action></div>
          <div className="guest-stats">{summaries.map(s => { const Icon = s.icon;
            return <div className="stat-card" key={s.label}><div className="stat-top"><span>{s.label}</span><span className="stat-icon blue"><Icon size={19}/></span></div>
              <div className="stat-value">{s.value}</div><div className="stat-note">{s.detail}</div></div>; })}</div>
          <div className="guest-grid">
            <section className="guest-card"><h2>Try a complete application</h2>
              <p>Create a fictional application, compare three sample offers, approve one, inject a lost response and reconcile it.</p>
              <div className="guest-steps"><span>1. Create</span><span>2. Compare</span><span>3. Approve</span><span>4. Reconcile</span></div>
              <Action onClick={() => change("applications")}>Open applications <ArrowRight size={16}/></Action></section>
            <section className="guest-card"><h2>Break the workflow on purpose</h2>
              <p>Run the safe and unsafe approaches side by side for a missing acknowledgement or duplicated response.</p>
              <div className="guest-highlight"><GitBranch size={19}/> Idempotency + evidence + recovery</div>
              <Action secondary onClick={() => change("laboratory")}>Explore reliability lab <ArrowRight size={16}/></Action></section>
          </div>
          <section className="guest-card"><h2>Recent synthetic applications</h2>
            {applications.length === 0 ? <p>No applications yet. Start one to explore the full simulated lifecycle.</p> :
              applications.map(app => <button className="guest-list-row" key={app.id}
                onClick={() => { setChosen(app.id); change("applications"); }}>
                <strong>{app.id}</strong><span>{rupees(app.amount)}</span><Badge stage={app.stage}/><ChevronRight size={16}/></button>)}</section>
        </>}
        {tab === "applications" && <>
          <div className="page-intro"><div><span className="eyebrow">BORROWER LIFECYCLE</span>
            <h1>Synthetic applications</h1><p>Create fictional cases and walk through an evidence-based submission.</p></div></div>
          <section className="guest-card">
            <div className="guest-card-title"><h2>New application</h2><span className="guest-muted">Illustrative INR amounts</span></div>
            <div className="guest-form">
              <label>Requested amount (₹)<input aria-label="Requested amount" type="number" min="1000" max="1000000" value={amount} onChange={e => setAmount(e.target.value)}/></label>
              <label>Monthly income (₹)<input aria-label="Monthly income" type="number" min="1" value={income} onChange={e => setIncome(e.target.value)}/></label>
              <label>Monthly debt (₹)<input aria-label="Monthly debt" type="number" min="0" value={debt} onChange={e => setDebt(e.target.value)}/></label>
              <label>Term (months)<input aria-label="Term months" type="number" min="6" max="120" value={months} onChange={e => setMonths(e.target.value)}/></label>
            </div>
            <Action onClick={create}><Plus size={16}/> Create synthetic application</Action>
          </section>
          {applications.length > 0 && <section className="guest-card">
            <div className="guest-card-title"><h2>Choose application</h2><span className="guest-muted">{applications.length} created</span></div>
            <div className="guest-app-tabs">{applications.map(a =>
              <button key={a.id} aria-pressed={chosen === a.id} className={chosen === a.id ? "selected" : ""}
                onClick={() => { setChosen(a.id); setSelectedOffer("LENDER-N"); }}>{a.id} <Badge stage={a.stage}/></button>)}</div>
          </section>}
          {current && <section className="guest-card">
            <div className="guest-card-title"><div><span className="eyebrow">APPLICATION {current.id}</span><h2>{rupees(current.amount)} request</h2></div><Badge stage={current.stage}/></div>
            <p>Monthly income {rupees(current.income)} · debt {rupees(current.debt)} · term {current.months} months</p>
            {current.stage === "draft" && <div className="guest-inline">
              <p>Consent and numeric checks are illustrated locally. Compare the sample lender terms to proceed.</p>
              <Action onClick={() => update(current.id, { stage: "compared" }, "Deterministic example offers compared and evidence snapshot recorded locally.")}>Compare three offers <ArrowRight size={16}/></Action></div>}
            {compareable && <>
              <h3>Illustrative offers</h3><div className="guest-offers">{OFFERS.map(offer =>
                <OfferCard key={offer.id} offer={offer} selected={selectedOffer === offer.id} onSelect={() => setSelectedOffer(offer.id)}/>)}</div>
              {current.stage === "compared" && <Action onClick={() => {
                const name = OFFERS.find(o => o.id === selectedOffer)?.name;
                update(current.id, { stage: "approved", lender: selectedOffer }, "Single-use synthetic approval recorded for " + name + ".");
              }}>Approve selected fictional offer <ArrowRight size={16}/></Action>}
            </>}
            {current.stage === "approved" && <div className="guest-scenario">
              <h3>Inject transport behavior</h3>
              <label htmlFor="submission-failure">Failure scenario</label>
              <select id="submission-failure" value={failure} onChange={e => setFailure(e.target.value as Failure)}>
                <option value="normal">Normal confirmation</option>
                <option value="lost">Effect committed, confirmation lost</option>
                <option value="duplicate">Duplicate confirmation</option></select>
              <p>The write-ahead intent, approval and lender effect are represented only in local demo state.</p>
              <Action onClick={submit}>Submit to simulated lender <ArrowRight size={16}/></Action>
            </div>}
            {current.stage === "submission_unknown" && <div className="guest-recovery"><CircleAlert size={21}/>
              <div><h3>Unknown is not failure</h3><p>The synthetic lender has recorded the effect.
                Safely reconcile the original idempotency key instead of creating another request.</p>
                <Action onClick={reconcile}>Reconcile original effect <RefreshCcw size={16}/></Action></div></div>}
            {current.stage === "submitted" && <div className="guest-success"><CheckCircle2 size={21}/>
              <div><strong>One effect verified.</strong><p>Zero duplicate simulated submissions were created.</p></div></div>}
            <h3>Evidence and audit timeline</h3><Timeline events={current.events}/>
          </section>}
        </>}
        {tab === "assistant" && <>
          <div className="page-intro"><div><span className="eyebrow">EVIDENCE-FOCUSED EXPLANATIONS</span>
            <h1>AI Intelligence</h1><p>Explore how an assistant should explain only verifiable claims.</p></div></div>
          <section className="guest-card guest-assistant">
            <div className="guest-highlight"><Bot size={20}/> Deterministic explanation preview — not a live language model</div>
            <label htmlFor="guest-question">Ask a reliability or workflow question</label>
            <textarea id="guest-question" value={question} maxLength={500} onChange={e => setQuestion(e.target.value)}
              rows={4} placeholder="Why must we reconcile a missing confirmation?"/>
            <div className="guest-suggestions">{[
              "Why must we reconcile a missing confirmation?",
              "How does idempotency prevent duplicate submissions?",
              "Why is consent necessary before evaluation?",
              "Where did these example offer rates come from?",
            ].map(q => <button key={q} onClick={() => setQuestion(q)}>{q}</button>)}</div>
            <Action onClick={explain} disabled={!question.trim()}>Explain with synthetic evidence <ArrowRight size={16}/></Action>
            {answer && <div className="guest-answer" role="status"><strong>Verity explanation</strong>
              {answer.split("\n\n").map((p, i) => <p key={i}>{p}</p>)}</div>}
          </section>
        </>}
        {tab === "laboratory" && <>
          <div className="page-intro"><div><span className="eyebrow">FAULT INJECTION</span>
            <h1>Reliability Lab</h1><p>Compare a guarded workflow with a deliberately unsafe baseline.</p></div></div>
          <section className="guest-card">
            <div className="guest-card-title"><h2>Configure experiment</h2><span className="guest-muted">Deterministic local simulation</span></div>
            <label htmlFor="guest-lab-scenario">Failure scenario</label>
            <select id="guest-lab-scenario" value={scenario} onChange={e => setScenario(e.target.value as Failure)}>
              <option value="lost">Effect committed, response lost</option>
              <option value="duplicate">Duplicate confirmation</option>
              <option value="normal">No injected failure</option></select>
            <p>{scenario === "lost" ? "A simulated provider commits the side effect but its acknowledgement vanishes." :
              scenario === "duplicate" ? "The same acknowledgement is delivered twice." :
                "A normal request receives one verified confirmation."}</p>
            <div className="guest-lab-actions"><Action onClick={() => runLab(true)}><ShieldCheck size={16}/> Run guarded strategy</Action>
              <Action secondary onClick={() => runLab(false)}><CircleAlert size={16}/> Run unsafe baseline</Action></div>
          </section>
          <div className="guest-grid">
            <section className="guest-card"><h2>Guarded strategy</h2>
              <p>Durable intent, bound approval, identical idempotency key, ledger reconciliation and conservative confirmations.</p>
              <div className="guest-metric"><Gauge size={24}/> Duplicate effects: 0 in the illustrated lost-response case</div></section>
            <section className="guest-card"><h2>Unsafe baseline</h2>
              <p>Blind retries with new request identifiers or unverified confirmation counting.</p>
              <div className="guest-metric danger"><CircleAlert size={24}/> May duplicate an effect or claim unverified success</div></section>
          </div>
          <section className="guest-card"><h2>Experiment results</h2>
            {runs.length === 0 ? <p>Run an experiment to compare outcomes.</p> :
              runs.map((r, index) => <div className="guest-lab-row" key={index}>
                <span className={"guest-stage " + (r.safe ? "good" : "warn")}>{r.safe ? "Guarded" : "Unsafe"}</span>
                <strong>{r.scenario === "lost" ? "Lost confirmation" : r.scenario === "duplicate" ? "Duplicate confirmation" : "Normal response"}</strong>
                <span>{r.outcome}</span></div>)}</section>
        </>}
      </div>
      <footer className="page-footer"><span>Verity / public reliability sandbox</span>
        <span>In-memory fictional data only · No real loan applications or lender submissions</span></footer>
    </div>
  </div>;
}
