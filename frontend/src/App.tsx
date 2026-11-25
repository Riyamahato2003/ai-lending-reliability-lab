import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import {
  Activity,
  ArrowRight,
  Bot,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  CircleDot,
  Copy,
  Fingerprint,
  FlaskConical,
  Gauge,
  GitBranch,
  Info,
  Layers3,
  LayoutDashboard,
  LockKeyhole,
  LogOut,
  Menu,
  Play,
  RefreshCcw,
  Send,
  Shield,
  ShieldCheck,
  Sparkles,
  WalletCards,
  X,
} from "lucide-react";
import {
  api,
  currency,
  date,
  short,
  title,
  type Consent,
  type Evidence,
  type Explanation,
  type LabRun,
  type Session,
  type Workflow,
} from "./api";

const MetricsChart = lazy(() => import("./MetricsChart"));
type Page = "overview" | "workflows" | "assistant" | "laboratory";
const SCENARIO_DESCRIPTIONS: Record<string, string> = {
  REQUEST_LOST_BEFORE_LENDER:
    "The first request never reaches the simulated lender. Recovery decides whether a retry is permitted.",
  EFFECT_COMMITTED_RESPONSE_LOST:
    "The lender effect commits, but its acknowledgement disappears. Observe idempotency and reconciliation.",
  DUPLICATE_RESPONSE:
    "A transport delivers the same confirmation twice. One provider effect should remain one effect.",
  CONFLICTING_RESPONSE:
    "The returned digest conflicts with the immutable dispatch intent and authoritative ledger.",
  RESPONSE_AFTER_DEADLINE:
    "The response arrives after the logical provider deadline and lease boundary.",
};
const ICONS = [LayoutDashboard, WalletCards, Bot, FlaskConical];
const NAV: [Page, string, string][] = [
  ["overview", "Overview", "Your workspace"],
  ["workflows", "Applications", "Borrower lifecycle"],
  ["assistant", "AI Intelligence", "Evidence-grounded"],
  ["laboratory", "Reliability Lab", "Failure experiments"],
];
const FIELDS: [string, string, string][] = [
  ["monthly_income_minor", "Monthly income (₹)", "4200"],
  ["monthly_debt_minor", "Monthly debt (₹)", "800"],
  ["requested_amount_minor", "Requested amount (₹)", "24000"],
  ["term_months", "Term (months)", "24"],
  ["employment_months", "Employment (months)", "36"],
  ["region", "Synthetic region", "synthetic-west"],
];

function StateTag({ value }: { value: string }) {
  const tone = /submitted|confirmed|completed|active|consented|compared/.test(
    value,
  )
    ? "positive"
    : /unknown|pending|awaiting|evaluating|dispatching|submitting|running/.test(
          value,
        )
      ? "amber"
      : /revoked|failed|ineligible|cancelled/.test(value)
        ? "negative"
        : "neutral";
  return (
    <span className={`state-tag ${tone}`}>
      <span className="state-dot" />
      {title(value)}
    </span>
  );
}
function Button({
  children,
  onClick,
  disabled = false,
  variant = "primary",
  type = "button",
  className = "",
}: {
  children: React.ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  variant?: "primary" | "secondary" | "ghost" | "danger";
  type?: "button" | "submit";
  className?: string;
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`btn btn-${variant} ${className}`}
    >
      {children}
    </button>
  );
}
function Panel({
  title: heading,
  subtitle,
  action,
  children,
  className = "",
}: {
  title: string;
  subtitle?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      <div className="panel-heading">
        <div>
          <h3>{heading}</h3>
          {subtitle && <p>{subtitle}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}
function Empty({
  icon: Icon = CircleDot,
  title: heading,
  description,
  children,
}: {
  icon?: typeof CircleDot;
  title: string;
  description: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">
        <Icon size={24} />
      </div>
      <h3>{heading}</h3>
      <p>{description}</p>
      {children}
    </div>
  );
}
type LoginMode = "demo" | "pilot";

function PublicTour({ onClose }: { onClose: () => void }) {
  const [scenario, setScenario] = useState<"lost" | "duplicate">("lost");
  const lost = scenario === "lost";
  return (
    <main className="public-tour">
      <header className="public-tour-header">
        <div className="brand">
          <span className="brand-mark"><Layers3 size={22} /></span>
          <span>verity<span className="brand-period">.</span></span>
        </div>
        <Button variant="secondary" onClick={onClose}>Return to sign in</Button>
      </header>
      <div className="public-tour-content">
        <div className="eyebrow"><FlaskConical size={16} /> PUBLIC INTERACTIVE EXPLAINER</div>
        <h1>What happens when a lender's confirmation goes missing?</h1>
        <p className="muted">Explore two reliability failure cases and compare how a guarded workflow
          behaves against a deliberately unsafe baseline. This tour uses fixed, fictional
          examples. No account, database write, live lender, or AI service is involved.</p>
        <div className="public-tour-selector" aria-label="Choose a failure scenario">
          <button type="button" aria-pressed={lost}
            className={lost ? "selected" : ""}
            onClick={() => setScenario("lost")}>Confirmation lost</button>
          <button type="button" aria-pressed={!lost}
            className={!lost ? "selected" : ""}
            onClick={() => setScenario("duplicate")}>Duplicate confirmation</button>
        </div>
        <div className="public-tour-grid">
          <section className="public-tour-panel">
            <div className="public-tour-label"><CircleAlert size={18} /> Failure injected</div>
            <h2>{lost ? "The effect happened, but the reply disappeared." :
              "The same response arrives twice."}</h2>
            <p>{lost
              ? "The fictional lender records one submission, but the network drops its acknowledgement before Verity sees it."
              : "An unreliable transport repeats a confirmation for the same fictional submission."}</p>
            <div className="public-tour-sequence">
              <span>1. Approval recorded</span>
              <span>2. Dispatch intent committed</span>
              <span>3. {lost ? "Response lost" : "Response repeated"}</span>
              <span>4. Recovery is required</span>
            </div>
          </section>
          <section className="public-tour-panel public-tour-safe">
            <div className="public-tour-label"><ShieldCheck size={18} /> Guarded approach</div>
            <h2>{lost ? "Keep the status unresolved until the ledger confirms it." :
              "Treat the repeated response as the same effect."}</h2>
            <p>{lost
              ? "Reuse the original idempotency key and reconcile against the simulated lender ledger before any retry."
              : "Match the confirmation to the original immutable intent; do not count a second lender effect."}</p>
            <div className="public-tour-result">Illustrative outcome: {lost
              ? "one existing effect can be confirmed without blindly resubmitting"
              : "one effect remains one effect"}</div>
          </section>
          <section className="public-tour-panel public-tour-unsafe">
            <div className="public-tour-label"><CircleAlert size={18} /> Unsafe teaching baseline</div>
            <h2>{lost ? "Blindly retry with a fresh key." :
              "Count every response as new work."}</h2>
            <p>{lost
              ? "A new request identifier can produce a duplicate simulated effect when the first one already succeeded."
              : "Treating delivery count as effect count creates a false picture of what the lender actually did."}</p>
            <div className="public-tour-result">Illustrative risk: {lost
              ? "a duplicate effect"
              : "a duplicate or unverified confirmation"}</div>
          </section>
        </div>
        <div className="public-tour-footer">
          <div><strong>Want to test the real synthetic workflow?</strong>
            <p>Borrower and operator workspaces require separately issued pilot credentials.
              Access to live workflows is not part of this public explainer.</p></div>
          <Button onClick={onClose}>Go to pilot sign in <ArrowRight size={17} /></Button>
        </div>
        <p className="public-tour-disclaimer">Educational simulation only. No real personal information,
          credit decision, lending offer, external lender transaction, or reliability guarantee.</p>
      </div>
    </main>
  );
}

function Login({ onLogin }: { onLogin: (s: Session, mode: LoginMode) => void }) {
  const [showPublicTour, setShowPublicTour] = useState(false);
  const [username, setUsername] = useState(""),
    [password, setPassword] = useState(""),
    [pending, setPending] = useState(false),
    [error, setError] = useState(""),
    [modeError, setModeError] = useState(""),
    [mode, setMode] = useState<LoginMode | null>(null);

  useEffect(() => {
    let cancelled = false;
    void api<{ login_mode: LoginMode }>("/auth/capabilities")
      .then((result) => {
        if (cancelled) return;
        if (result.login_mode !== "demo" && result.login_mode !== "pilot") {
          throw new Error("Unsupported authentication mode");
        }
        setMode(result.login_mode);
      })
      .catch(() => {
        if (!cancelled) setModeError("Cannot verify the sign-in environment. Please try again later.");
      });
    return () => { cancelled = true; };
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!mode) return;
    setPending(true);
    setError("");
    try {
      onLogin(
        await api<Session>(mode === "pilot" ? "/pilot/sessions" : "/demo/sessions",
          undefined, "POST", { username, password }),
        mode,
      );
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setPending(false);
    }
  }
  if (showPublicTour) return <PublicTour onClose={() => setShowPublicTour(false)} />;

  return (
    <div className="login-shell">
      <div className="login-aside">
        <div className="brand brand-light">
          <span className="brand-mark">
            <Layers3 size={23} />
          </span>
          <span>
            verity<span className="brand-period">.</span>
          </span>
        </div>
        <div className="login-aside-copy">
          <div className="eyebrow light">
            <span className="pulse-dot" /> A RELIABILITY-FIRST EXPERIMENT
          </div>
          <h1>
            Trust is built
            <br />
            in the <em>details.</em>
          </h1>
          <p>
            Explore how verified evidence, explicit consent, and fault-tolerant
            engineering change the way simulated lending works.
          </p>
          <div className="login-traits">
            <span>
              <ShieldCheck size={16} /> Evidence first
            </span>
            <span>
              <GitBranch size={16} /> Auditable by design
            </span>
            <span>
              <FlaskConical size={16} /> Fully synthetic
            </span>
          </div>
        </div>
        <div className="aside-bottom">
          THE AI LENDING RELIABILITY LAB <span>2026 / SYNTHETIC</span>
        </div>
        <div className="orb orb-one" />
        <div className="orb orb-two" />
      </div>
      <div className="login-main">
        <div className="login-form">
          <span className="eyebrow">WORKSPACE ACCESS</span>
          <h2>Welcome to Verity</h2>
          <p className="muted">
            {mode === "pilot"
              ? "This pilot is invitation-only. Use your separately issued credentials to explore synthetic lending workflows."
              : "Sign in to explore the synthetic lending workflow and operator experiment lab."}
          </p>
          <button type="button" className="public-tour-link" onClick={() => setShowPublicTour(true)}>
            Explore the public reliability walkthrough <ArrowRight size={16} />
          </button>
          {mode === "demo" && <div className="demo-switch">
            <button
              className={username === "borrower-001" ? "selected" : ""}
              onClick={() => {
                setUsername("borrower-001");
                setPassword("");
              }}
            >
              Borrower demo
            </button>
            <button
              className={username === "lab-operator" ? "selected" : ""}
              onClick={() => {
                setUsername("lab-operator");
                setPassword("");
              }}
            >
              Operator lab
            </button>
          </div>}
          <form onSubmit={submit}>
            <label>
              {mode === "pilot" ? "Email" : "Demo username"}
              <input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                required
              />
            </label>
            <label>
              Password
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
              />
            </label>
            {(error || modeError) && (
              <div className="inline-error">
                <CircleAlert size={16} />
                {error || modeError}
              </div>
            )}
            <Button type="submit" disabled={pending || mode === null} className="full">
              {pending ? (
                "Signing in…"
              ) : (
                <>
                  Enter workspace <ArrowRight size={17} />
                </>
              )}
            </Button>
          </form>
          <div className="login-disclaimer">
            <Shield size={16} />
            <span>
              No real lending, credit decisions, or customer information. All
              borrowers, offers, and effects are illustrative.
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function App() {
  const [session, setSession] = useState<Session | null>(null),
    [loginMode, setLoginMode] = useState<LoginMode | null>(null),
    [page, setPage] = useState<Page>("overview"),
    [workflows, setWorkflows] = useState<Workflow[]>([]),
    [selected, setSelected] = useState<string | null>(null),
    [consent, setConsent] = useState<Consent | null>(null),
    [toast, setToast] = useState<{ text: string; bad?: boolean } | null>(null),
    [busy, setBusy] = useState(false),
    [mobileNav, setMobileNav] = useState(false);
  const token = session?.access_token || "",
    operator = session?.role === "operator";
  const alert = useCallback(
    (text: string, bad = false) => setToast({ text, bad }),
    [],
  );
  const load = useCallback(async () => {
    if (!session || operator) return;
    try {
      const [w, c] = await Promise.all([
        api<Workflow[]>("/workflows", token),
        api<Consent>("/assistant/consent", token),
      ]);
      setWorkflows(w);
      setConsent(c);
      setSelected((old) =>
        old && w.some((v) => v.id === old) ? old : w[0]?.id || null,
      );
    } catch (e) {
      alert((e as Error).message, true);
    }
  }, [session, operator, token, alert]);
  useEffect(() => {
    void load();
  }, [load]);
  function navigate(next: Page) {
    setPage(next);
    setMobileNav(false);
  }
  async function logout() {
    try {
      await api(loginMode === "pilot" ? "/pilot/sessions/logout" : "/demo/sessions/logout", token, "POST");
    } catch {}
    setSession(null);
    setLoginMode(null);
    setSelected(null);
    setWorkflows([]);
    setToast(null);
  }
  const current = workflows.find((w) => w.id === selected);
  const navItems = operator
    ? NAV.filter((x) => x[0] === "overview" || x[0] === "laboratory")
    : NAV;
  if (!session) return <Login onLogin={(s, mode) => { setSession(s); setLoginMode(mode); }} />;
  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileNav ? "open" : ""}`}>
        <div className="side-top">
          <div className="brand">
            <span className="brand-mark">
              <Layers3 size={22} />
            </span>
            <span>
              verity<span className="brand-period">.</span>
            </span>
          </div>
          <button
            className="close-mobile"
            onClick={() => setMobileNav(false)}
            aria-label="Close menu"
          >
            <X size={20} />
          </button>
          <div className="workspace-label">
            WORKSPACE <ChevronDown size={13} />
          </div>
          <div className="workspace-card">
            <span className="workspace-icon">
              <ShieldCheck size={20} />
            </span>
            <div>
              <strong>Reliability Lab</strong>
              <small>{loginMode === "pilot" ? "Invitation-only synthetic pilot" : "Local synthetic environment"}</small>
            </div>
          </div>
          <div className="navigation-label">EXPLORE</div>
          <nav aria-label="Main navigation">
            {navItems.map(([key, label, description]) => {
              const Icon = ICONS[NAV.findIndex((x) => x[0] === key)];
              return (
                <button
                  key={key}
                  className={`nav-item ${page === key ? "active" : ""}`}
                  onClick={() => navigate(key)}
                >
                  <Icon size={19} />
                  <span>
                    {label}
                    <small>{description}</small>
                  </span>
                  {page === key && <span className="nav-indicator" />}
                </button>
              );
            })}
          </nav>
        </div>
        <div className="side-bottom">
          <div className="environment">
            <span className="pulse-dot" /> SYSTEM ONLINE{" "}
            <span className="env-line" />
          </div>
          <div className="side-account">
            <div className="avatar">
              {operator ? "OP" : session.borrower_id?.slice(-2)}
            </div>
            <div>
              <strong>{operator ? "Lab operator" : session.borrower_id}</strong>
              <small>
                {operator ? "Restricted experiment role" : "Synthetic borrower"}
              </small>
            </div>
            <button
              onClick={() => void logout()}
              title="Sign out"
              aria-label="Sign out"
            >
              <LogOut size={18} />
            </button>
          </div>
        </div>
      </aside>
      <div className="main-area">
        <header className="topbar">
          <button
            className="menu-trigger"
            onClick={() => setMobileNav((v) => !v)}
            aria-label="Open menu"
          >
            <Menu size={22} />
          </button>
          <span className="crumb">
            Workspace <ChevronRight size={14} />{" "}
            <b>{NAV.find((n) => n[0] === page)?.[1]}</b>
          </span>
          <div className="top-actions">
            <span className="demo-chip">
              <span /> SYNTHETIC DEMO
            </span>
            <button
              className="icon-btn"
              onClick={() => void load()}
              aria-label="Refresh workspace"
            >
              <RefreshCcw size={17} />
            </button>
            <div className="top-avatar">{operator ? "O" : "B"}</div>
          </div>
        </header>
        <div className="page-content">
          {operator ? (
            page === "laboratory" ? (
              <Laboratory token={token} alert={alert} />
            ) : (
              <OperatorOverview onNavigate={() => navigate("laboratory")} />
            )
          ) : page === "overview" ? (
            <BorrowerOverview
              workflows={workflows}
              select={(id) => {
                setSelected(id);
                navigate("workflows");
              }}
              onCreate={() => navigate("workflows")}
              onAI={() => navigate("assistant")}
            />
          ) : page === "workflows" ? (
            <WorkflowPage
              token={token}
              workflows={workflows}
              selected={selected}
              setSelected={setSelected}
              current={current}
              refresh={load}
              alert={alert}
              busy={busy}
              setBusy={setBusy}
            />
          ) : page === "assistant" ? (
            <AssistantPage
              token={token}
              workflow={current}
              workflows={workflows}
              selected={selected}
              setSelected={setSelected}
              consent={consent}
              refresh={load}
              alert={alert}
            />
          ) : null}
        </div>
        <footer className="page-footer">
          <span>Verity / AI Lending Reliability Lab</span>
          <span>
            Illustrative financial data only · No real lender submissions
          </span>
        </footer>
      </div>
      {toast && (
        <div role="status" className={`toast ${toast.bad ? "error" : ""}`}>
          <span>
            {toast.bad ? <CircleAlert size={17} /> : <CheckCircle2 size={17} />}
          </span>
          {toast.text}
          <button onClick={() => setToast(null)} aria-label="Dismiss">
            <X size={15} />
          </button>
        </div>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  note,
  icon: Icon,
  color = "blue",
}: {
  label: string;
  value: string | number;
  note: string;
  icon: typeof Activity;
  color?: string;
}) {
  return (
    <div className="stat-card">
      <div className="stat-top">
        <span>{label}</span>
        <span className={`stat-icon ${color}`}>
          <Icon size={19} />
        </span>
      </div>
      <div className="stat-value">{value}</div>
      <div className="stat-note">{note}</div>
    </div>
  );
}
function BorrowerOverview({
  workflows,
  select,
  onCreate,
  onAI,
}: {
  workflows: Workflow[];
  select: (id: string) => void;
  onCreate: () => void;
  onAI: () => void;
}) {
  const count = (states: string[]) =>
    workflows.filter((w) => states.includes(w.state)).length;
  return (
    <>
      <div className="page-intro">
        <div>
          <span className="eyebrow">BORROWER WORKSPACE</span>
          <h1>
            Good afternoon <span className="wave">✳</span>
          </h1>
          <p>Clarity across every step of your synthetic lending journey.</p>
        </div>
        <Button onClick={onCreate}>
          <span>Manage applications</span>
          <ArrowRight size={17} />
        </Button>
      </div>
      <div className="stats-grid">
        <Stat
          label="Total applications"
          value={workflows.length}
          note="All recorded workflows"
          icon={WalletCards}
        />
        <Stat
          label="In evaluation"
          value={count([
            "consented",
            "evaluating",
            "compared",
            "awaiting_approval",
            "submitting",
          ])}
          note="Active workflow stages"
          icon={Activity}
          color="violet"
        />
        <Stat
          label="Submitted workflows"
          value={count(["submitted"])}
          note="Verify ledger in workflow details"
          icon={ShieldCheck}
          color="mint"
        />
        <Stat
          label="Needs attention"
          value={count([
            "submission_unknown",
            "failed",
            "consent_revoked",
            "ineligible",
          ])}
          note="Review evidence and next steps"
          icon={CircleAlert}
          color="amber"
        />
      </div>
      <div className="two-col">
        <Panel
          title="Recent applications"
          subtitle="Your latest verified synthetic workflows"
          action={
            <button className="link-btn" onClick={onCreate}>
              View all <ArrowRight size={15} />
            </button>
          }
        >
          {workflows.length ? (
            <div className="compact-list">
              {workflows.slice(0, 5).map((w) => (
                <button
                  key={w.id}
                  onClick={() => select(w.id)}
                  className="list-row"
                >
                  <span className="row-symbol">
                    <WalletCards size={18} />
                  </span>
                  <span className="list-primary">
                    <b>Application {short(w.id)}</b>
                    <small>
                      {date(w.created_at)} · v{w.version}
                    </small>
                  </span>
                  <StateTag value={w.state} />
                  <ChevronRight size={17} />
                </button>
              ))}
            </div>
          ) : (
            <Empty
              icon={WalletCards}
              title="No applications yet"
              description="Start with synthetic details and explicit lending consent."
            >
              <Button onClick={onCreate}>
                Create application <ArrowRight size={16} />
              </Button>
            </Empty>
          )}
        </Panel>
        <Panel
          title="The trust architecture"
          subtitle="Reliability isn't a promise. It's observable."
        >
          <div className="feature-cards">
            <div className="feature-row">
              <span className="feature-icon mint">
                <Fingerprint size={18} />
              </span>
              <div>
                <strong>Identity-bound actions</strong>
                <p>
                  Every workflow operation is scoped to your borrower identity.
                </p>
              </div>
            </div>
            <div className="feature-row">
              <span className="feature-icon violet">
                <GitBranch size={18} />
              </span>
              <div>
                <strong>Evidence, not guesses</strong>
                <p>
                  Immutable audit trails and simulated ledger reconciliation.
                </p>
              </div>
            </div>
            <div className="feature-row">
              <span className="feature-icon blue">
                <Bot size={18} />
              </span>
              <div>
                <strong>AI with boundaries</strong>
                <p>
                  Read-only explanation tools; no model-authorized submissions.
                </p>
              </div>
            </div>
          </div>
          <Button variant="secondary" className="full" onClick={onAI}>
            Explore the AI assistant <ArrowRight size={16} />
          </Button>
        </Panel>
      </div>
      <div className="footer-insight">
        <Sparkles size={18} />
        <div>
          <strong>Built to be examined, not just experienced.</strong>
          <p>
            Every simulated action is an opportunity to observe deterministic
            decisions, safe failure handling, and explicit consent.
          </p>
        </div>
      </div>
    </>
  );
}

function WorkflowPage({
  token,
  workflows,
  selected,
  setSelected,
  current,
  refresh,
  alert,
  busy,
  setBusy,
}: {
  token: string;
  workflows: Workflow[];
  selected: string | null;
  setSelected: (id: string) => void;
  current?: Workflow;
  refresh: () => Promise<void>;
  alert: (s: string, b?: boolean) => void;
  busy: boolean;
  setBusy: (s: boolean) => void;
}) {
  const [creating, setCreating] = useState(false),
    [financial, setFinancial] = useState<Record<string, string>>({
      monthly_income_minor: "4200",
      monthly_debt_minor: "800",
      requested_amount_minor: "24000",
      term_months: "24",
      employment_months: "36",
      region: "synthetic-west",
    }),
    [lendingText, setLendingText] = useState(""),
    [ack, setAck] = useState(false),
    [evidence, setEvidence] = useState<Record<string, Evidence>>({}),
    [approval, setApproval] = useState<{
      approval_id: string;
      workflow_version: number;
    } | null>(null),
    [lender, setLender] = useState("cedar"),
    [tab, setTab] = useState<"offers" | "rules" | "timeline">("offers"),
    [confirmation, setConfirmation] = useState<"consume" | "revoke" | null>(
      null,
    );
  const invoke = async (fn: () => Promise<unknown>, ok: string) => {
    setBusy(true);
    try {
      await fn();
      alert(ok);
      await refresh();
      if (selected) await loadEvidence(selected);
    } catch (e) {
      alert((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  const loadEvidence = useCallback(
    async (id: string) => {
      try {
        const entries = await Promise.all(
          ["status", "rules", "offers", "requirements"].map(
            async (key) =>
              [
                key,
                await api<Evidence>(`/workflows/${id}/evidence/${key}`, token),
              ] as const,
          ),
        );
        setEvidence(Object.fromEntries(entries));
      } catch (e) {
        alert((e as Error).message, true);
      }
    },
    [token, alert],
  );
  useEffect(() => {
    if (selected) void loadEvidence(selected);
    else setEvidence({});
  }, [selected, loadEvidence]);
  useEffect(() => {
    api<{ consent_text: string }>(
      "/consent-text/illustrative_offer_comparison",
      token,
    )
      .then((x) => setLendingText(x.consent_text))
      .catch(() => {});
  }, [token]);
  const offers = (evidence.offers?.facts?.offers || []) as any[],
    rules = (evidence.rules?.facts?.lenders || []) as any[],
    audit = (evidence.status?.facts?.audit || []) as any[],
    requirements = evidence.requirements?.facts,
    status = evidence.status?.facts;
  async function create() {
    if (!ack)
      return alert("Read and accept the purpose-specific consent text.", true);
    const numbers = Object.fromEntries(
      Object.entries(financial).map(([k, v]) => [
        k,
        k === "region"
          ? v
          : k.endsWith("_minor")
            ? Math.round(Number(v) * 100)
            : Number(v),
      ]),
    );
    await invoke(async () => {
      const w = await api<Workflow>("/workflows", token, "POST", {
        purpose: "illustrative_offer_comparison",
        consent_text: lendingText,
        financial_details: numbers,
      });
      setSelected(w.id);
      setCreating(false);
      setAck(false);
    }, "New synthetic workflow created");
  }
  return (
    <>
      <div className="page-intro">
        <div>
          <span className="eyebrow">APPLICATION LIFECYCLE</span>
          <h1>Applications</h1>
          <p>
            Consent, compare, approve, and inspect every step of the workflow.
          </p>
        </div>
        <Button onClick={() => setCreating((v) => !v)}>
          {creating ? "Close form" : "New application"}{" "}
          {creating ? <X size={17} /> : <ArrowRight size={17} />}
        </Button>
      </div>
      {creating && (
        <Panel
          title="Create an illustrative application"
          subtitle="No real credit check, borrower data, or lender is involved."
        >
          <div className="form-grid">
            {FIELDS.map(([key, label]) => (
              <label key={key} className="field-label">
                {label}
                <input
                  type={key === "region" ? "text" : "number"}
                  min={key === "region" ? undefined : 0}
                  step={
                    key === "region"
                      ? undefined
                      : key.endsWith("_minor")
                        ? "0.01"
                        : "1"
                  }
                  value={financial[key] || ""}
                  onChange={(e) =>
                    setFinancial((x) => ({ ...x, [key]: e.target.value }))
                  }
                />
              </label>
            ))}
          </div>
          <div className="consent-box">
            <div className="consent-title">
              <ShieldCheck size={18} /> Explicit lending consent
            </div>
            <p>{lendingText || "Loading immutable consent wording…"}</p>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={ack}
                onChange={(e) => setAck(e.target.checked)}
              />{" "}
              I agree to this purpose-specific synthetic comparison.
            </label>
          </div>
          <Button
            disabled={busy || !ack || !lendingText}
            onClick={() => void create()}
          >
            Create synthetic workflow <ArrowRight size={16} />
          </Button>
        </Panel>
      )}
      <div className="application-layout">
        <div className="application-sidebar">
          <div className="subsection-label">
            YOUR WORKFLOWS <span>{workflows.length}</span>
          </div>
          {workflows.length ? (
            workflows.map((w) => (
              <button
                key={w.id}
                className={`application-choice ${selected === w.id ? "selected" : ""}`}
                onClick={() => {
                  setSelected(w.id);
                  setApproval(null);
                }}
              >
                <div className="choice-head">
                  <WalletCards size={17} />
                  <span>{short(w.id)}</span>
                  <ChevronRight size={16} />
                </div>
                <StateTag value={w.state} />
                <small>{date(w.created_at)}</small>
              </button>
            ))
          ) : (
            <p className="muted small">Create your first workflow to begin.</p>
          )}
        </div>
        <div className="application-body">
          {!current ? (
            <Empty
              title="Select an application"
              description="Choose a workflow to explore evidence, offers, and actions."
            />
          ) : (
            <>
              <div className="detail-heading">
                <div>
                  <div className="eyebrow">
                    APPLICATION / {short(current.id).toUpperCase()}
                  </div>
                  <h2>Workflow details</h2>
                  <p className="muted">
                    Policy {current.policy_version} · Version {current.version}
                  </p>
                </div>
                <StateTag value={current.state} />
              </div>
              <div className="progress-track">
                {[
                  "Consent",
                  "Evaluation",
                  "Comparison",
                  "Approval",
                  "Submission",
                ].map((s, i) => (
                  <div className="progress-step" key={s}>
                    <span
                      className={`step-circle ${i === 0 || (["compared", "awaiting_approval", "submitting", "dispatching", "submitted", "submission_unknown", "ineligible"].includes(current.state) && i <= 2) || (["submitting", "dispatching", "submitted", "submission_unknown"].includes(current.state) && i <= 3) || current.state === "submitted" ? "done" : ""}`}
                    >
                      {i + 1}
                    </span>
                    <span>{s}</span>
                  </div>
                ))}
              </div>
              <div className="detail-status">
                <span className="feature-icon blue">
                  <Info size={19} />
                </span>
                <div>
                  <strong>
                    {title(status?.submission || current.state)} status
                  </strong>
                  <p>
                    {status?.submission === "confirmed"
                      ? "Simulator ledger confirms this synthetic effect."
                      : status?.submission === "uncertain"
                        ? "Outcome remains uncertain; a timeout is not a rejection."
                        : "No confirmed simulated effect has been established."}
                  </p>
                </div>
              </div>
              <div className="actionbar">
                {current.state === "consented" && (
                  <Button
                    disabled={busy}
                    onClick={() =>
                      void invoke(
                        () =>
                          api(
                            `/workflows/${current.id}/evaluate`,
                            token,
                            "POST",
                          ),
                        "Deterministic evaluation complete",
                      )
                    }
                  >
                    <Play size={16} /> Run evaluation
                  </Button>
                )}
                {current.state === "compared" && (
                  <>
                    <select
                      aria-label="Choose simulated lender"
                      value={lender}
                      onChange={(e) => setLender(e.target.value)}
                      className="select-input"
                    >
                      {["cedar", "harbor", "summit"].map((v) => (
                        <option key={v} value={v}>
                          {title(v)}
                        </option>
                      ))}
                    </select>
                    <Button
                      disabled={
                        busy ||
                        !offers.some(
                          (o) => o.lender_id === lender && !o.expired,
                        )
                      }
                      onClick={() =>
                        void invoke(async () => {
                          const a = await api<{
                            approval_id: string;
                            workflow_version: number;
                          }>(
                            `/workflows/${current.id}/approvals`,
                            token,
                            "POST",
                            {
                              lender_id: lender,
                              expected_version: current.version,
                            },
                          );
                          setApproval(a);
                        }, "Approval issued; review before consumption")
                      }
                    >
                      Request approval
                    </Button>
                  </>
                )}
                {current.state === "awaiting_approval" && (
                  <Button
                    disabled={
                      busy || requirements?.approval?.status !== "pending"
                    }
                    onClick={() => setConfirmation("consume")}
                  >
                    <LockKeyhole size={16} /> Confirm single-use approval
                  </Button>
                )}
                {!["consent_revoked", "submitted", "cancelled"].includes(
                  current.state,
                ) && (
                  <Button
                    variant="danger"
                    disabled={busy || current.consent_status === "revoked"}
                    onClick={() => setConfirmation("revoke")}
                  >
                    Revoke lending consent
                  </Button>
                )}
                <Button
                  variant="ghost"
                  onClick={() => void loadEvidence(current.id)}
                >
                  <RefreshCcw size={16} /> Refresh evidence
                </Button>
              </div>
              {approval && (
                <div className="notice-note">
                  <CheckCircle2 size={17} /> Approval{" "}
                  {short(approval.approval_id)} created. You must explicitly
                  confirm its consumption to authorize the queued simulated
                  dispatch.
                </div>
              )}
              {confirmation && (
                <div className="confirm-strip">
                  <CircleAlert size={19} />
                  <div>
                    <strong>
                      {confirmation === "consume"
                        ? "Confirm single-use approval?"
                        : "Revoke lending consent?"}
                    </strong>
                    <p>
                      {confirmation === "consume"
                        ? "This is a separate authorized action. It will queue the synthetic submission."
                        : "This deletes live sensitive workflow payloads and prevents new simulated effects. Already committed effects remain reconcilable."}
                    </p>
                  </div>
                  <Button
                    disabled={busy}
                    variant={confirmation === "revoke" ? "danger" : "primary"}
                    onClick={() => {
                      const operation = confirmation;
                      setConfirmation(null);
                      void invoke(
                        () =>
                          operation === "revoke"
                            ? api(
                                `/consents/${current.consent_grant_id}/revoke`,
                                token,
                                "POST",
                              )
                            : api(
                                `/workflows/${current.id}/approvals/${approval?.approval_id || requirements?.approval?.id || "missing"}/consume`,
                                token,
                                "POST",
                                {
                                  expected_workflow_version:
                                    approval?.workflow_version ??
                                    requirements?.approval?.workflow_version ??
                                    current.version,
                                },
                              ),
                        operation === "revoke"
                          ? "Lending consent revoked"
                          : "Approval consumed and dispatch queued",
                      );
                    }}
                  >
                    Confirm
                  </Button>
                  <button
                    className="icon-btn"
                    onClick={() => setConfirmation(null)}
                  >
                    <X size={17} />
                  </button>
                </div>
              )}
              <div className="tabbar">
                {(
                  [
                    ["offers", "Simulated offers"],
                    ["rules", "Rule evidence"],
                    ["timeline", "Audit timeline"],
                  ] as const
                ).map(([value, label]) => (
                  <button
                    className={tab === value ? "active" : ""}
                    key={value}
                    onClick={() => setTab(value)}
                  >
                    {label}
                  </button>
                ))}
              </div>
              {tab === "offers" ? (
                <div className="offer-list">
                  {offers.length ? (
                    offers.map((o: any) => (
                      <div className="offer-card" key={o.lender_id}>
                        <div className="offer-left">
                          <span className="lender-icon">
                            {o.lender_id.charAt(0).toUpperCase()}
                          </span>
                          <div>
                            <strong>{title(o.lender_id)}</strong>
                            <small>
                              Illustrative offer · {o.lender_version}
                            </small>
                          </div>
                        </div>
                        <div className="offer-values">
                          <div>
                            <small>Total repayment</small>
                            <strong>
                              {currency(
                                o.illustrative_values?.total_repayment_minor,
                              )}
                            </strong>
                          </div>
                          <div>
                            <small>Term</small>
                            <strong>
                              {o.illustrative_values?.term_months} mo
                            </strong>
                          </div>
                        </div>
                        <StateTag value={o.expired ? "expired" : "available"} />
                      </div>
                    ))
                  ) : (
                    <Empty
                      icon={WalletCards}
                      title="No available offers"
                      description="Run evaluation first; eligible lenders may produce simulated offers."
                    />
                  )}
                  <p className="tiny-muted">
                    Always sorted by estimated total repayment, then lender ID.
                    These are not real offers.
                  </p>
                </div>
              ) : tab === "rules" ? (
                <div className="rules-list">
                  {rules.length ? (
                    rules.map((r: any) => (
                      <div className="rule-group" key={r.lender_id}>
                        <div className="rule-group-header">
                          <strong>{title(r.lender_id)}</strong>
                          <StateTag
                            value={
                              r.eligible_under_demo_rules
                                ? "eligible under demo rules"
                                : "not eligible"
                            }
                          />
                        </div>
                        {r.rule_trace?.map((rule: any, i: number) => (
                          <div className="rule-row" key={i}>
                            <span
                              className={
                                rule.outcome === "pass"
                                  ? "rule-pass"
                                  : "rule-fail"
                              }
                            >
                              {rule.outcome === "pass" ? (
                                <Check size={15} />
                              ) : (
                                <X size={15} />
                              )}
                            </span>
                            <div>
                              <strong>{rule.rule_id}</strong>
                              <small>
                                {rule.field}: observed {String(rule.observed)} ·
                                required {String(rule.expected)}
                              </small>
                            </div>
                          </div>
                        ))}
                      </div>
                    ))
                  ) : (
                    <Empty
                      title="No rule trace yet"
                      description="This application has not been evaluated, or its sensitive results were deleted."
                    />
                  )}
                </div>
              ) : (
                <div className="timeline">
                  {audit.length ? (
                    audit.map((a: any) => (
                      <div className="timeline-row" key={a.sequence}>
                        <span className="timeline-node" />
                        <div>
                          <strong>
                            {title(a.action.replaceAll(".", " "))}
                          </strong>
                          <small>
                            {date(a.created_at)} · #{a.sequence}
                          </small>
                        </div>
                        <span className="timeline-state">
                          {a.state && title(a.state)}
                        </span>
                      </div>
                    ))
                  ) : (
                    <Empty
                      title="No audit events"
                      description="Events will appear when the workflow progresses."
                    />
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </>
  );
}

function AssistantPage({
  token,
  workflow,
  workflows,
  selected,
  setSelected,
  consent,
  refresh,
  alert,
}: {
  token: string;
  workflow?: Workflow;
  workflows: Workflow[];
  selected: string | null;
  setSelected: (x: string) => void;
  consent: Consent | null;
  refresh: () => Promise<void>;
  alert: (t: string, b?: boolean) => void;
}) {
  const [question, setQuestion] = useState(
      "Why is my application still pending?",
    ),
    [answers, setAnswers] = useState<
      { question: string; result: Explanation }[]
    >([]),
    [busy, setBusy] = useState(false),
    [granting, setGranting] = useState(false);
  async function ask(q = question) {
    if (!workflow || !q.trim()) return;
    setBusy(true);
    try {
      const x = await api<Explanation>(
        `/workflows/${workflow.id}/assistant/explain`,
        token,
        "POST",
        { question: q.trim() },
      );
      setAnswers((prev) => [...prev, { question: q, result: x }]);
      setQuestion("");
    } catch (e) {
      alert((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  }
  async function consentAction() {
    if (!consent) return;
    setGranting(true);
    try {
      if (consent.status === "active" && consent.grant_id) {
        await api(
          `/assistant/consent/${consent.grant_id}/revoke`,
          token,
          "POST",
        );
        alert("External AI-processing consent revoked");
      } else {
        await api("/assistant/consent", token, "POST", {
          consent_text: consent.consent_text,
        });
        alert("External AI-processing consent recorded");
      }
      await refresh();
    } catch (e) {
      alert((e as Error).message, true);
    } finally {
      setGranting(false);
    }
  }
  return (
    <>
      <div className="page-intro">
        <div>
          <span className="eyebrow">EVIDENCE-GROUNDED INTELLIGENCE</span>
          <h1>
            Ask what the evidence says<span className="blue-period">.</span>
          </h1>
          <p>
            The assistant reads verified records. It cannot issue decisions or
            take actions.
          </p>
        </div>
        <span className="assistant-mark">
          <Sparkles size={28} />
        </span>
      </div>
      <div className="assistant-layout">
        <div className="assistant-chat">
          <div className="chat-top">
            <div className="bot-avatar">
              <Bot size={20} />
            </div>
            <div>
              <strong>Verity Intelligence</strong>
              <small>
                <span className="pulse-dot" /> Read-only · server-verified facts
              </small>
            </div>
            <div className="chat-badge">
              <ShieldCheck size={14} /> Deterministic answers
            </div>
          </div>
          <div className="chat-messages">
            {answers.length ? (
              answers.map((entry, i) => (
                <div key={i} className="message-pair">
                  <div className="message-user">{entry.question}</div>
                  <div className="message-ai">
                    <span className="message-avatar">
                      <Sparkles size={16} />
                    </span>
                    <div>
                      <p>{entry.result.answer}</p>
                      <div className="message-meta">
                        <span>
                          <Fingerprint size={13} />{" "}
                          {entry.result.tool_identifier.replaceAll("_", " ")}
                        </span>
                        <span>{entry.result.explanation_mode} mode</span>
                        {entry.result.fallback_reason && (
                          <span>Fallback: {entry.result.fallback_reason}</span>
                        )}
                      </div>
                      <div className="evidence-tags">
                        {entry.result.evidence_references
                          .slice(0, 4)
                          .map((r, j) => (
                            <span key={j} title={r.identifier}>
                              {r.kind} · {short(r.identifier)}
                            </span>
                          ))}
                      </div>
                    </div>
                  </div>
                </div>
              ))
            ) : (
              <div className="chat-welcome">
                <div className="welcome-spark">
                  <Sparkles size={28} />
                </div>
                <h2>Answers with receipts.</h2>
                <p>
                  Ask about eligibility, offer comparisons, submission status,
                  or what consent and approvals mean.
                </p>
                <div className="suggested-questions">
                  {[
                    "Why am I not eligible under the demo rules?",
                    "Which offer has the lowest estimated repayment?",
                    "Has the simulated lender confirmed my submission?",
                    "What happens if I withdraw consent?",
                  ].map((q) => (
                    <button
                      key={q}
                      disabled={!workflow || busy}
                      onClick={() => void ask(q)}
                    >
                      {q} <ArrowRight size={14} />
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
          <form
            className="chat-composer"
            onSubmit={(e) => {
              e.preventDefault();
              void ask();
            }}
          >
            <input
              aria-label="Question for assistant"
              placeholder={
                workflow
                  ? "Ask about this synthetic workflow…"
                  : "Select a workflow first…"
              }
              disabled={!workflow || busy}
              maxLength={1000}
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
            />
            <button
              aria-label="Send question"
              disabled={!workflow || busy || !question.trim()}
              type="submit"
            >
              <Send size={19} />
            </button>
          </form>
          <div className="chat-foot">
            No AI writes · No real lending outcomes · Evidence from server-owned
            records
          </div>
        </div>
        <aside className="assistant-aside">
          <Panel
            title="Current context"
            subtitle="The assistant uses only the selected workflow."
          >
            <label className="field-label">
              Active workflow
              <select
                className="select-input full"
                value={selected || ""}
                onChange={(e) => setSelected(e.target.value)}
              >
                {workflows.length ? (
                  workflows.map((w) => (
                    <option key={w.id} value={w.id}>
                      {short(w.id)} — {title(w.state)}
                    </option>
                  ))
                ) : (
                  <option value="">No workflows yet</option>
                )}
              </select>
            </label>
            {workflow && (
              <div className="context-summary">
                <StateTag value={workflow.state} />
                <small>Policy: {workflow.policy_version}</small>
                <small>Workflow ID: {short(workflow.id)}…</small>
              </div>
            )}
          </Panel>
          <Panel
            title="External AI consent"
            subtitle="Optional question classification by Gemini."
          >
            <div className="consent-state">
              <ShieldCheck size={20} />
              <div>
                <strong>
                  {consent?.status === "active"
                    ? "Consent active"
                    : "Local mode available"}
                </strong>
                <p>
                  {consent?.status === "active"
                    ? "You may revoke external AI access."
                    : "Deterministic explanations work without sharing questions externally."}
                </p>
              </div>
            </div>
            {consent && (
              <details className="disclosure">
                <summary>Read consent disclosure</summary>
                <p>{consent.consent_text}</p>
              </details>
            )}
            <Button
              variant="secondary"
              className="full"
              disabled={!consent || granting}
              onClick={() => void consentAction()}
            >
              {consent?.status === "active"
                ? "Revoke AI consent"
                : "Grant optional AI consent"}
            </Button>
          </Panel>
          <div className="guardrail-card">
            <LockKeyhole size={18} />
            <strong>Protected by design</strong>
            <p>
              The model chooses an explanation topic, never the facts. All
              financial decisions, approvals, and submissions remain outside AI
              control.
            </p>
          </div>
        </aside>
      </div>
    </>
  );
}

function OperatorOverview({ onNavigate }: { onNavigate: () => void }) {
  return (
    <>
      <div className="page-intro">
        <div>
          <span className="eyebrow">OPERATOR WORKSPACE</span>
          <h1>Observe the failure. Understand the recovery.</h1>
          <p>
            Controlled experiments isolate fault behavior from live borrower
            workflows.
          </p>
        </div>
        <Button onClick={onNavigate}>
          Open Reliability Lab <ArrowRight size={17} />
        </Button>
      </div>
      <div className="operator-hero">
        <div>
          <span className="eyebrow light">SYSTEM DESIGN / MEASURED</span>
          <h2>
            Make resilience
            <br />
            <em>visible.</em>
          </h2>
          <p>
            Five deterministic fault scenarios. Two isolated implementations.
            One way to inspect what actually happens when things go wrong.
          </p>
          <Button onClick={onNavigate}>
            Run an experiment <ArrowRight size={17} />
          </Button>
        </div>
        <div className="hero-diagram">
          <span className="diagram-label">FAULT-INJECTION PIPELINE</span>
          <div className="diagram-step">
            Dispatch intent <CheckCircle2 size={18} />
          </div>
          <div className="diagram-connector" />
          <div className="diagram-step accented">
            Controlled fault <Activity size={18} />
          </div>
          <div className="diagram-connector" />
          <div className="diagram-step">
            Evidence & reconciliation <ShieldCheck size={18} />
          </div>
        </div>
      </div>
      <div className="stats-grid">
        <Stat
          label="Fault scenarios"
          value="05"
          note="Fixed, reproducible schedules"
          icon={FlaskConical}
        />
        <Stat
          label="Implementations"
          value="02"
          note="Safe + deliberately unsafe"
          icon={GitBranch}
          color="violet"
        />
        <Stat
          label="Trial upper limit"
          value="100"
          note="Per bounded experiment"
          icon={Gauge}
          color="amber"
        />
        <Stat
          label="Live lender access"
          value="None"
          note="Fully simulated ledger"
          icon={ShieldCheck}
          color="mint"
        />
      </div>
    </>
  );
}

function Laboratory({
  token,
  alert,
}: {
  token: string;
  alert: (t: string, b?: boolean) => void;
}) {
  const [scenario, setScenario] = useState("EFFECT_COMMITTED_RESPONSE_LOST"),
    [trials, setTrials] = useState(5),
    [seed, setSeed] = useState(2468),
    [revoke, setRevoke] = useState(false),
    [running, setRunning] = useState(false),
    [comparison, setComparison] = useState<{
      safe_run: LabRun;
      unsafe_run: LabRun;
      schedule_hash: string;
    } | null>(null),
    [history, setHistory] = useState<LabRun[]>([]),
    [selectedRun, setSelectedRun] = useState<LabRun | null>(null);
  const loadHistory = useCallback(async () => {
    try {
      setHistory(await api<LabRun[]>("/operator/lab/runs", token));
    } catch {}
  }, [token]);
  useEffect(() => {
    void loadHistory();
  }, [loadHistory]);
  const canRevoke = [
    "REQUEST_LOST_BEFORE_LENDER",
    "EFFECT_COMMITTED_RESPONSE_LOST",
  ].includes(scenario);
  async function processApproved() {
    try {
      const result = await api<{ status: string }>(
        "/operator/dispatch/process-next",
        token,
        "POST",
      );
      alert("Dispatch worker result: " + title(result.status));
    } catch (e) {
      alert((e as Error).message, true);
    }
  }
  async function run() {
    setRunning(true);
    setComparison(null);
    try {
      const config = {
        scenario,
        seed,
        trials,
        revoke_before_recovery: revoke && canRevoke,
      };
      const safe = await api<LabRun>("/operator/lab/runs", token, "POST", {
        ...config,
        implementation: "safe",
      });
      const unsafe = await api<LabRun>("/operator/lab/runs", token, "POST", {
        ...config,
        implementation: "unsafe",
      });
      const cmp = await api<{
        safe_run: LabRun;
        unsafe_run: LabRun;
        schedule_hash: string;
      }>("/operator/lab/compare", token, "POST", {
        safe_run_id: safe.id,
        unsafe_run_id: unsafe.id,
      });
      setComparison(cmp);
      setSelectedRun(await api<LabRun>(`/operator/lab/runs/${safe.id}`, token));
      await loadHistory();
      alert("Both experiments finished on the same deterministic schedule");
    } catch (e) {
      alert((e as Error).message, true);
      await loadHistory();
    } finally {
      setRunning(false);
    }
  }
  const plot = comparison
    ? [
        {
          name: "Safe",
          effects: comparison.safe_run.effects_created,
          duplicates: comparison.safe_run.duplicate_effects,
          confirmed: comparison.safe_run.confirmed_effects,
        },
        {
          name: "Unsafe",
          effects: comparison.unsafe_run.effects_created,
          duplicates: comparison.unsafe_run.duplicate_effects,
          confirmed: comparison.unsafe_run.confirmed_effects,
        },
      ]
    : [];
  const max = useMemo(
    () =>
      comparison
        ? Math.max(
            comparison.safe_run.effects_created,
            comparison.unsafe_run.effects_created,
          )
        : 0,
    [comparison],
  );
  return (
    <>
      <div className="page-intro">
        <div>
          <span className="eyebrow">OPERATOR / FAULT INJECTION</span>
          <h1>
            Reliability Lab<span className="blue-period">.</span>
          </h1>
          <p>
            Reproduce failure, inspect outcomes, and compare the actual measured
            effects.
          </p>
        </div>
        <div className="lab-header-actions">
          <Button variant="secondary" onClick={() => void processApproved()}>
            <Play size={15} /> Process next approved dispatch
          </Button>
          <span className="lab-icon">
            <FlaskConical size={27} />
          </span>
        </div>
      </div>
      <div className="lab-layout">
        <div className="lab-control">
          <Panel
            title="Experiment configuration"
            subtitle="Runs create isolated synthetic records."
          >
            <div className="field-stack">
              <label className="field-label">
                Failure scenario
                <select
                  className="select-input full"
                  value={scenario}
                  onChange={(e) => {
                    setScenario(e.target.value);
                    setRevoke(false);
                  }}
                >
                  {Object.keys(SCENARIO_DESCRIPTIONS).map((s) => (
                    <option key={s} value={s}>
                      {title(s)}
                    </option>
                  ))}
                </select>
              </label>
              <div className="scenario-help">
                <Info size={17} />
                <p>{SCENARIO_DESCRIPTIONS[scenario]}</p>
              </div>
              <label className="field-label">
                Trial count <span className="field-hint">1 to 100</span>
                <input
                  type="number"
                  min={1}
                  max={100}
                  value={trials}
                  onChange={(e) => setTrials(Number(e.target.value))}
                />
              </label>
              <label className="field-label">
                Deterministic seed
                <input
                  type="number"
                  min={0}
                  value={seed}
                  onChange={(e) => setSeed(Number(e.target.value))}
                />
              </label>
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  disabled={!canRevoke}
                  checked={revoke && canRevoke}
                  onChange={(e) => setRevoke(e.target.checked)}
                />{" "}
                Inject consent revocation
              </label>
              <div className="config-note">
                <LockKeyhole size={16} />
                <span>
                  Safe and unsafe runs share a logical fault schedule; they
                  never access borrower workflows.
                </span>
              </div>
              <Button
                className="full"
                disabled={
                  running ||
                  trials < 1 ||
                  trials > 100 ||
                  seed < 0 ||
                  !Number.isSafeInteger(seed)
                }
                onClick={() => void run()}
              >
                {running ? (
                  "Running experiments…"
                ) : (
                  <>
                    Run safe / unsafe comparison <Play size={17} />
                  </>
                )}
              </Button>
            </div>
          </Panel>
          <Panel
            title="Recent experiment runs"
            subtitle="Immutable run history in the lab ledger."
          >
            <div className="compact-list">
              {history.length ? (
                history.slice(0, 8).map((x) => (
                  <button
                    className="list-row"
                    key={x.id}
                    onClick={async () => {
                      try {
                        setSelectedRun(
                          await api<LabRun>(
                            `/operator/lab/runs/${x.id}`,
                            token,
                          ),
                        );
                      } catch (e) {
                        alert((e as Error).message, true);
                      }
                    }}
                  >
                    <span className="row-symbol">
                      <Activity size={17} />
                    </span>
                    <span className="list-primary">
                      <b>
                        {title(x.implementation)} · {short(x.id)}
                      </b>
                      <small>{title(x.scenario)}</small>
                    </span>
                    <StateTag value={x.run_status} />
                  </button>
                ))
              ) : (
                <p className="muted small">
                  No experiments yet. Run a comparison above.
                </p>
              )}
            </div>
          </Panel>
        </div>
        <div className="lab-results">
          {comparison ? (
            <>
              <div className="result-hero">
                <span className="eyebrow">
                  OBSERVED MEASUREMENTS / {comparison.safe_run.trials} TRIALS
                </span>
                <h2>
                  Same fault.
                  <br />
                  Different outcome.
                </h2>
                <p>
                  Only the intentionally unsafe baseline omits the idempotency
                  and reconciliation controls.
                </p>
                <span className="hash">
                  Schedule #{comparison.schedule_hash.slice(0, 14)}
                </span>
              </div>
              <div className="comparison-stats">
                <Stat
                  label="Safe effects"
                  value={comparison.safe_run.effects_created}
                  note={`${comparison.safe_run.confirmed_effects} ledger-confirmed`}
                  icon={ShieldCheck}
                  color="mint"
                />
                <Stat
                  label="Unsafe duplicates"
                  value={comparison.unsafe_run.duplicate_effects}
                  note={`${comparison.unsafe_run.effects_created} effects in total`}
                  icon={CircleAlert}
                  color="amber"
                />
                <Stat
                  label="Safe violations"
                  value={Object.values(
                    comparison.safe_run.invariant_violations,
                  ).reduce((a, b) => a + b, 0)}
                  note="Observed in this sample"
                  icon={Fingerprint}
                />
              </div>
              <Panel
                title="Effects vs. authoritative confirmation"
                subtitle="Actual row counts from both isolated experiment executions."
              >
                <div className="chart-wrap">
                  <Suspense
                    fallback={
                      <div className="chart-loading">Loading chart…</div>
                    }
                  >
                    <MetricsChart data={plot} max={max} />
                  </Suspense>
                </div>
              </Panel>
              <Panel
                title="Invariant accounting"
                subtitle="Explicit violations, observed over the configured number of trials."
              >
                <div className="invariants">
                  {Object.keys(comparison.safe_run.invariant_violations).map(
                    (name) => (
                      <div className="invariant-row" key={name}>
                        <span>{name.replaceAll("_", " ")}</span>
                        <span className="value-good">
                          {comparison.safe_run.violation_trials[name] || 0}/
                          {comparison.safe_run.trials} safe
                        </span>
                        <span className="value-bad">
                          {comparison.unsafe_run.violation_trials[name] || 0}/
                          {comparison.unsafe_run.trials} unsafe
                        </span>
                      </div>
                    ),
                  )}
                </div>
              </Panel>
            </>
          ) : (
            <div className="lab-placeholder">
              <div className="placeholder-graphic">
                <div className="placeholder-rings">
                  <span />
                  <span />
                  <span />
                </div>
                <GitBranch size={36} />
              </div>
              <div className="eyebrow">EXPERIMENT READY</div>
              <h2>
                Make the invisible
                <br />
                failure visible.
              </h2>
              <p>
                Choose a scenario, set a trial count, and run the same
                deterministic schedule through two isolated implementations.
              </p>
              <div className="placeholder-sequence">
                <span>01 · Inject</span>
                <ArrowRight size={15} />
                <span>02 · Reconcile</span>
                <ArrowRight size={15} />
                <span>03 · Compare</span>
              </div>
            </div>
          )}
          {selectedRun && (
            <Panel
              title="Trial-level evidence"
              subtitle={`${title(selectedRun.implementation)} run · ${short(selectedRun.id)} · ${selectedRun.trials} trials`}
              action={
                <button
                  className="link-btn"
                  onClick={() => {
                    navigator.clipboard?.writeText(selectedRun.id);
                    alert("Run ID copied");
                  }}
                >
                  <Copy size={15} /> Copy ID
                </button>
              }
            >
              <div className="trial-table-wrap">
                <table className="trial-table">
                  <thead>
                    <tr>
                      <th>Trial</th>
                      <th>State</th>
                      <th>Attempts</th>
                      <th>Effects</th>
                      <th>Duplicates</th>
                      <th>Confirmed</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(selectedRun.trial_outcomes || [])
                      .slice(0, 100)
                      .map((o: any) => (
                        <tr key={o.trial_index}>
                          <td>{o.trial_index + 1}</td>
                          <td>
                            <StateTag value={o.final_state} />
                          </td>
                          <td>{o.provider_attempts}</td>
                          <td>{o.effects_created}</td>
                          <td>{o.duplicate_effects}</td>
                          <td>{o.confirmed_effects}</td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            </Panel>
          )}
        </div>
      </div>
      <div className="footer-insight">
        <Info size={18} />
        <div>
          <strong>What these numbers do—and do not—prove.</strong>
          <p>
            They describe finite deterministic simulations inside isolated
            PostgreSQL lab tables, not real lender effects or a universal
            reliability guarantee. The unsafe baseline is intentionally
            deficient.
          </p>
        </div>
      </div>
    </>
  );
}
