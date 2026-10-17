import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  ArrowDownToLine,
  ArrowLeft,
  ArrowUpFromLine,
  BriefcaseBusiness,
  CalendarClock,
  Check,
  CheckCircle2,
  ChevronDown,
  CircleAlert,
  Clock3,
  ExternalLink,
  FileText,
  Filter,
  Github,
  LoaderCircle,
  Mail,
  MapPin,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  ShieldCheck,
  Sparkles,
  Trash2,
  Upload,
  UserRound,
  X,
} from "lucide-react";

const STATUSES = [
  { value: "discovered", label: "Discovered" },
  { value: "shortlisted", label: "Shortlisted" },
  { value: "ready", label: "Ready to review" },
  { value: "applied", label: "Applied" },
  { value: "interview", label: "Interview" },
  { value: "offer", label: "Offer" },
  { value: "rejected", label: "Rejected" },
  { value: "withdrawn", label: "Withdrawn" },
];

const BADGE_CLASSES = {
  discovered: "bg-amber-50 text-amber-700 ring-amber-200",
  shortlisted: "bg-indigo-50 text-indigo-700 ring-indigo-200",
  ready: "bg-blue-50 text-blue-700 ring-blue-200",
  applied: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  interview: "bg-violet-50 text-violet-700 ring-violet-200",
  offer: "bg-teal-50 text-teal-700 ring-teal-200",
  rejected: "bg-rose-50 text-rose-700 ring-rose-200",
  withdrawn: "bg-slate-100 text-slate-600 ring-slate-200",
};

const DEFAULT_EDIT_FORM = {
  company: "",
  title: "",
  url: "",
  location: "",
  status: "discovered",
  applied_date: "",
  next_action: "",
  resume_used: "",
  notes: "",
};

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body !== undefined && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(path, { ...options, headers, cache: "no-store" });
  let payload;
  try {
    if ((response.headers.get("content-type") || "").includes("application/json")) {
      payload = await response.json();
    } else {
      payload = {};
    }
  } catch {
    payload = {};
  }
  if (!response.ok) {
    throw new Error(payload.error || "Request failed (" + response.status + ").");
  }
  return payload;
}

function todayISO() {
  const now = new Date();
  return [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, "0"),
    String(now.getDate()).padStart(2, "0"),
  ].join("-");
}

function formatDate(value) {
  if (!value) return "—";
  const parsed = new Date(String(value).slice(0, 10) + "T12:00:00");
  return Number.isNaN(parsed.getTime())
    ? value
    : new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" }).format(parsed);
}

function formatDateTime(value) {
  if (!value) return "Never";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : new Intl.DateTimeFormat(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      }).format(parsed);
}

function statusLabel(value) {
  return STATUSES.find((item) => item.value === value)?.label || value || "Discovered";
}

function Button({ children, onClick, variant = "secondary", size = "normal", disabled = false, type = "button", className = "", ...props }) {
  const base = "inline-flex items-center justify-center gap-2 rounded-lg border font-semibold transition focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-blue-100 disabled:cursor-not-allowed disabled:opacity-50";
  const sizes = size === "small" ? "min-h-8 px-2.5 text-xs" : "min-h-10 px-3.5 text-sm";
  const variants = {
    primary: "border-blue-700 bg-blue-700 text-white shadow-sm hover:border-blue-800 hover:bg-blue-800",
    secondary: "border-slate-200 bg-white text-slate-700 shadow-sm hover:bg-slate-50",
    ghost: "border-transparent bg-transparent text-slate-600 hover:bg-slate-100",
    danger: "border-rose-200 bg-white text-rose-700 hover:bg-rose-50",
  };
  return (
    <button {...props} type={type} onClick={onClick} disabled={disabled} className={[base, sizes, variants[variant] || variants.secondary, className].join(" ")}>
      {children}
    </button>
  );
}

function Pill({ children, tone = "slate" }) {
  const tones = {
    green: "bg-emerald-50 text-emerald-700 ring-emerald-200",
    slate: "bg-slate-100 text-slate-600 ring-slate-200",
    blue: "bg-blue-50 text-blue-700 ring-blue-200",
    amber: "bg-amber-50 text-amber-700 ring-amber-200",
    red: "bg-rose-50 text-rose-700 ring-rose-200",
  };
  return <span className={"inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ring-inset " + (tones[tone] || tones.slate)}>{children}</span>;
}

function StatusBadge({ status }) {
  return (
    <span className={"inline-flex whitespace-nowrap rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ring-inset " + (BADGE_CLASSES[status] || BADGE_CLASSES.discovered)}>
      {statusLabel(status)}
    </span>
  );
}

function PanelHeading({ title, description, action, count }) {
  return (
    <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h2 className="flex items-center gap-2 text-base font-semibold tracking-tight text-slate-900">
          {title}
          {count !== undefined && <span className="rounded-md bg-slate-100 px-2 py-0.5 text-xs font-semibold text-slate-600">{count}</span>}
        </h2>
        {description && <p className="mt-1 text-sm text-slate-500">{description}</p>}
      </div>
      {action}
    </div>
  );
}

function Modal({ title, description, onClose, children, footer, wide = false }) {
  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-slate-950/40 p-0 backdrop-blur-sm sm:items-center sm:p-5" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section role="dialog" aria-modal="true" aria-label={title} className={"max-h-[92vh] w-full overflow-y-auto rounded-t-2xl bg-white shadow-2xl sm:rounded-2xl " + (wide ? "max-w-3xl" : "max-w-xl")}>
        <header className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-slate-100 bg-white px-5 py-4 sm:px-6">
          <div>
            <h2 className="text-lg font-semibold tracking-tight text-slate-900">{title}</h2>
            {description && <p className="mt-1 text-sm leading-5 text-slate-500">{description}</p>}
          </div>
          <button onClick={onClose} type="button" aria-label="Close dialog" className="rounded-lg p-2 text-slate-400 hover:bg-slate-100 hover:text-slate-700"><X size={18} /></button>
        </header>
        <div className="px-5 py-5 sm:px-6">{children}</div>
        {footer && <footer className="flex flex-wrap justify-end gap-2 border-t border-slate-100 bg-slate-50/80 px-5 py-4 sm:px-6">{footer}</footer>}
      </section>
    </div>
  );
}

function TextField({ label, value, onChange, type = "text", required = false, placeholder = "", wide = false, maxLength, rows }) {
  const classes = "mt-1.5 w-full rounded-lg border border-slate-200 bg-white px-3 py-2.5 text-sm text-slate-800 shadow-sm outline-none transition placeholder:text-slate-400 hover:border-slate-300 focus:border-blue-500 focus:ring-4 focus:ring-blue-50";
  return (
    <label className={"block text-sm font-medium text-slate-700 " + (wide ? "sm:col-span-2" : "")}>
      {label}
      {rows ? (
        <textarea className={classes + " min-h-24 resize-y"} rows={rows} value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} maxLength={maxLength} />
      ) : (
        <input className={classes} type={type} value={value} onChange={(event) => onChange(event.target.value)} required={required} placeholder={placeholder} maxLength={maxLength} />
      )}
    </label>
  );
}

function SelectField({ label, value, onChange, children, disabled = false }) {
  return (
    <label className="block text-sm font-medium text-slate-700">
      {label}
      <select className="mt-1.5 min-h-10 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-800 shadow-sm outline-none focus:border-blue-500 focus:ring-4 focus:ring-blue-50 disabled:bg-slate-100" value={value} onChange={(event) => onChange(event.target.value)} disabled={disabled}>
        {children}
      </select>
    </label>
  );
}

function EmptyState({ icon: Icon = BriefcaseBusiness, title, description }) {
  return (
    <div className="flex min-h-48 flex-col items-center justify-center px-5 py-10 text-center">
      <span className="mb-3 grid size-11 place-items-center rounded-xl bg-slate-100 text-slate-500"><Icon size={21} /></span>
      <h3 className="text-sm font-semibold text-slate-800">{title}</h3>
      <p className="mt-1 max-w-sm text-sm text-slate-500">{description}</p>
    </div>
  );
}

function GmailSetupPage() {
  return (
    <main className="min-h-screen bg-slate-50 px-4 py-8 sm:px-6">
      <div className="mx-auto max-w-3xl">
        <a href="/" className="mb-6 inline-flex items-center gap-2 text-sm font-semibold text-slate-600 hover:text-blue-700"><ArrowLeft size={16} /> Back to dashboard</a>
        <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm sm:p-9">
          <div className="mb-6 flex size-12 items-center justify-center rounded-xl bg-blue-50 text-blue-700"><Mail size={24} /></div>
          <p className="text-xs font-bold uppercase tracking-[0.16em] text-blue-700">Job-Agent setup</p>
          <h1 className="mt-2 text-2xl font-bold tracking-tight text-slate-950 sm:text-3xl">Connect Gmail</h1>
          <p className="mt-3 text-sm leading-6 text-slate-600">Job-Agent uses the Gmail API with read-only access. Your OAuth token and the application tracker remain on this computer. The integration never sends, deletes, archives, or labels email.</p>

          <div className="mt-8 space-y-7">
            <section>
              <h2 className="font-semibold text-slate-900">1. Install the optional Gmail dependencies</h2>
              <pre className="mt-3 overflow-x-auto rounded-xl bg-slate-950 p-4 text-sm leading-6 text-slate-100"><code>python -m pip install -e ".[gmail]"</code></pre>
            </section>
            <section>
              <h2 className="font-semibold text-slate-900">2. Enable the Gmail API</h2>
              <ol className="mt-3 list-decimal space-y-2 pl-5 text-sm leading-6 text-slate-600">
                <li>Open <a className="font-medium text-blue-700 underline underline-offset-2" href="https://console.cloud.google.com/" target="_blank" rel="noreferrer">Google Cloud Console</a> and create or select a project.</li>
                <li>Enable the <a className="font-medium text-blue-700 underline underline-offset-2" href="https://console.cloud.google.com/apis/library/gmail.googleapis.com" target="_blank" rel="noreferrer">Gmail API</a>.</li>
              </ol>
            </section>
            <section>
              <h2 className="font-semibold text-slate-900">3. Create a Desktop OAuth client</h2>
              <ol className="mt-3 list-decimal space-y-2 pl-5 text-sm leading-6 text-slate-600">
                <li>Open Google Auth Platform and configure the app branding and audience.</li>
                <li>For a personal Gmail account, select <strong>External</strong>. While the app is in <strong>Testing</strong>, add your Google account under <strong>Test users</strong>.</li>
                <li>Under <strong>Clients</strong>, create an OAuth client ID with application type <strong>Desktop app</strong>.</li>
                <li>Download the client JSON and save it as <code className="rounded bg-slate-100 px-1.5 py-0.5 text-xs">config/gmail-credentials.json</code> in the repository.</li>
              </ol>
              <div className="mt-3 flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm leading-5 text-amber-900"><CircleAlert size={17} className="mt-0.5 shrink-0" /> Keep the OAuth client file private. Job-Agent ignores it in Git. Never commit it to a public repository.</div>
            </section>
            <section>
              <h2 className="font-semibold text-slate-900">4. Connect and sync</h2>
              <pre className="mt-3 overflow-x-auto rounded-xl bg-slate-950 p-4 text-sm leading-6 text-slate-100"><code>job-agent email-connect{"\n"}job-agent email-status{"\n"}job-agent email-sync</code></pre>
              <p className="mt-3 text-sm leading-6 text-slate-600">The first command opens Google's OAuth consent flow. Sign in to the account added as a test user and grant Gmail read-only permissions. The token is saved under <code className="rounded bg-slate-100 px-1.5 py-0.5 text-xs">data/gmail/</code>.</p>
            </section>
            <section>
              <h2 className="font-semibold text-slate-900">5. Use the dashboard review queue</h2>
              <p className="mt-2 text-sm leading-6 text-slate-600">In the dashboard, use <strong>Sync now</strong> for a manual sync or enable automatic syncing. While the dashboard is running, enabled syncing checks every 15 minutes. High-confidence matches may update a tracked application; ambiguous matches wait for your approval.</p>
            </section>
          </div>

          <div className="mt-8 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm leading-6 text-amber-900"><strong>OAuth testing note:</strong> Google refresh tokens for External apps in Testing can expire after seven days when Gmail scopes are requested. Reconnect if the integration stops syncing. Public distribution using Gmail restricted scopes may require Google verification and additional compliance work.</div>
          <div className="mt-6 flex flex-wrap gap-3 text-sm">
            <a href="https://developers.google.com/workspace/gmail/api/quickstart/python" target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-medium text-blue-700 hover:underline">Gmail API quickstart <ExternalLink size={14} /></a>
            <a href="https://developers.google.com/workspace/gmail/api/auth/scopes" target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-medium text-blue-700 hover:underline">Gmail API scopes <ExternalLink size={14} /></a>
            <a href="https://myaccount.google.com/connections" target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-medium text-blue-700 hover:underline">Manage Google connections <ExternalLink size={14} /></a>
          </div>
        </section>
      </div>
    </main>
  );
}

export default function App() {
  const [jobs, setJobs] = useState([]);
  const [resumes, setResumes] = useState([]);
  const [emailStatus, setEmailStatus] = useState({ available: false, connected: false, account: "", last_sync_at: null, auto_sync: false, pending_review: 0 });
  const [reviewItems, setReviewItems] = useState([]);
  const [reviewJobs, setReviewJobs] = useState([]);
  const [reviewJobByMessage, setReviewJobByMessage] = useState({});
  const [jobUrl, setJobUrl] = useState("");
  const [searchText, setSearchText] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [lastSyncedAt, setLastSyncedAt] = useState(null);
  const [currentTask, setCurrentTask] = useState(null);
  const [editing, setEditing] = useState(null);
  const [editForm, setEditForm] = useState(DEFAULT_EDIT_FORM);
  const [assistJob, setAssistJob] = useState(null);
  const [selectedResume, setSelectedResume] = useState("");
  const [browserMode, setBrowserMode] = useState("chrome");
  const [toastMessage, setToastMessage] = useState(null);
  const [saving, setSaving] = useState(false);
  const fileInputRef = useRef(null);
  const toastTimerRef = useRef(null);

  const notify = useCallback((message, kind = "success") => {
    setToastMessage({ message, kind });
    if (toastTimerRef.current) window.clearTimeout(toastTimerRef.current);
    toastTimerRef.current = window.setTimeout(() => setToastMessage(null), 4200);
  }, []);

  const loadJobs = useCallback(async () => {
    const result = await api("/api/jobs");
    setJobs(Array.isArray(result.jobs) ? result.jobs : []);
    setLastSyncedAt(new Date());
  }, []);

  const loadResumes = useCallback(async () => {
    const result = await api("/api/resumes");
    const items = Array.isArray(result.resumes) ? result.resumes : [];
    setResumes(items);
    setSelectedResume((previous) => previous || items[0] || "");
  }, []);

  const loadEmailStatus = useCallback(async () => {
    const result = await api("/api/email/status");
    setEmailStatus(result);
  }, []);

  const loadEmailReview = useCallback(async () => {
    const result = await api("/api/email/review");
    setReviewItems(Array.isArray(result.items) ? result.items : []);
    setReviewJobs(Array.isArray(result.jobs) ? result.jobs : []);
  }, []);

  const refreshDashboard = useCallback(async () => {
    const results = await Promise.allSettled([loadJobs(), loadResumes(), loadEmailStatus(), loadEmailReview()]);
    const failures = results.filter((result) => result.status === "rejected");
    if (failures.length) {
      notify("Some dashboard data could not be refreshed: " + failures[0].reason.message, "error");
    }
  }, [loadJobs, loadResumes, loadEmailStatus, loadEmailReview, notify]);

  useEffect(() => {
    void refreshDashboard();
    api("/api/tasks").then((result) => {
      if (result.latest) setCurrentTask(result.latest);
    }).catch(() => {});
    const refreshTimer = window.setInterval(() => { void refreshDashboard(); }, 60000);
    return () => {
      window.clearInterval(refreshTimer);
      if (toastTimerRef.current) window.clearTimeout(toastTimerRef.current);
    };
  }, [refreshDashboard]);

  useEffect(() => {
    if (!currentTask || currentTask.status !== "running") return undefined;
    let cancelled = false;
    const poll = async () => {
      try {
        const task = await api("/api/tasks/" + encodeURIComponent(currentTask.id));
        if (cancelled) return;
        setCurrentTask(task);
        if (task.status !== "running") {
          await refreshDashboard();
          notify(task.status === "succeeded" ? (task.label || "Task") + " completed." : (task.label || "Task") + " failed. Check command activity.", task.status === "succeeded" ? "success" : "error");
        }
      } catch (error) {
        if (!cancelled) notify("Could not read command output: " + error.message, "error");
      }
    };
    const poller = window.setInterval(() => { void poll(); }, 900);
    void poll();
    return () => {
      cancelled = true;
      window.clearInterval(poller);
    };
  }, [currentTask && currentTask.id, currentTask && currentTask.status, refreshDashboard, notify]);

  const startTask = async (path, payload, label) => {
    try {
      const result = await api(path, { method: "POST", body: JSON.stringify(payload) });
      if (result.task_id) {
        setCurrentTask({ ...result, label: label || result.label, output: "Starting task…" });
        notify((label || result.label || "Task") + " started. If it requests input, use the terminal running Job-Agent.");
        return true;
      }
      await refreshDashboard();
      return true;
    } catch (error) {
      notify(error.message, "error");
      return false;
    }
  };

  const onAddJob = async (event) => {
    event.preventDefault();
    const value = jobUrl.trim();
    if (!value) return;
    const started = await startTask("/api/actions/add-job", { url: value }, "Add job");
    if (started) setJobUrl("");
  };

  const openEdit = (job = null) => {
    setEditForm(job ? {
      company: job.company || "",
      title: job.title || "",
      url: job.url || "",
      location: job.location || "",
      status: job.status || "discovered",
      applied_date: job.applied_date || "",
      next_action: job.next_action || "",
      resume_used: job.resume_used || "",
      notes: job.notes || "",
    } : { ...DEFAULT_EDIT_FORM });
    setEditing(job ? { id: Number(job.id) } : { id: null });
  };

  const saveEdit = async (event) => {
    event.preventDefault();
    if (!editForm.company.trim() || !editForm.title.trim() || !editForm.url.trim()) {
      notify("Company, role and URL are required.", "error");
      return;
    }
    setSaving(true);
    try {
      const payload = {
        ...editForm,
        company: editForm.company.trim(),
        title: editForm.title.trim(),
        url: editForm.url.trim(),
        location: editForm.location.trim(),
        resume_used: editForm.resume_used.trim(),
        notes: editForm.notes.trim(),
      };
      if (editing.id) {
        await api("/api/jobs/" + editing.id, { method: "PATCH", body: JSON.stringify(payload) });
        notify("Application updated.");
      } else {
        await api("/api/actions/add-manual-job", { method: "POST", body: JSON.stringify(payload) });
        notify("Application added.");
      }
      setEditing(null);
      await Promise.all([loadJobs(), loadEmailReview()]);
    } catch (error) {
      notify(error.message, "error");
    } finally {
      setSaving(false);
    }
  };

  const updateStatus = async (job, status) => {
    try {
      await api("/api/jobs/" + job.id, { method: "PATCH", body: JSON.stringify({ status }) });
      await loadJobs();
      notify("Status updated.");
    } catch (error) {
      notify(error.message, "error");
    }
  };

  const deleteJob = async (job) => {
    const label = (job.company || "Unknown company") + " — " + (job.title || "Untitled role");
    if (!window.confirm("Delete this application?\n\n" + label + "\n\nThis cannot be undone.")) return;
    try {
      await api("/api/jobs/" + job.id, { method: "DELETE" });
      await loadJobs();
      notify("Application deleted.");
    } catch (error) {
      notify(error.message, "error");
    }
  };

  const openAssist = async (job) => {
    setAssistJob(job);
    setBrowserMode("chrome");
    try {
      await loadResumes();
    } catch (error) {
      notify("Could not load resumes: " + error.message, "error");
    }
  };

  const beginApply = async (event) => {
    event.preventDefault();
    if (!assistJob || !selectedResume) {
      notify("Choose a resume file first.", "error");
      return;
    }
    const jobId = Number(assistJob.id);
    const resume = selectedResume;
    const mode = browserMode;
    setAssistJob(null);
    await startTask("/api/actions/apply", { job_id: jobId, resume, browser: mode }, "Assist with application");
  };

  const exportCsv = async () => {
    try {
      const response = await fetch("/api/export.csv", { cache: "no-store" });
      if (!response.ok) {
        let payload = {};
        try { payload = await response.json(); } catch {}
        throw new Error(payload.error || "CSV export failed.");
      }
      const blob = await response.blob();
      const downloadUrl = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = downloadUrl;
      anchor.download = "job-applications.csv";
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(downloadUrl);
      notify("Exported " + jobs.length + " applications to CSV.");
    } catch (error) {
      notify(error.message, "error");
    }
  };

  const importCsv = async (event) => {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".csv")) {
      notify("Choose a .csv file.", "error");
      event.target.value = "";
      return;
    }
    if (file.size > 5 * 1024 * 1024) {
      notify("CSV files must be 5 MB or smaller.", "error");
      event.target.value = "";
      return;
    }
    try {
      const csvText = await file.text();
      if (!window.confirm("Import " + file.name + "?\n\nRows are matched by job URL. Matching applications will be updated and new URLs added. Applications absent from the file will not be deleted. A database backup is created before changes.")) return;
      const result = await api("/api/import.csv", { method: "POST", body: JSON.stringify({ csv_text: csvText }) });
      await loadJobs();
      const details = result.import || {};
      notify("Import complete: " + (details.inserted || 0) + " added, " + (details.updated || 0) + " updated. Backup saved.");
    } catch (error) {
      notify("CSV import failed: " + error.message, "error");
    } finally {
      event.target.value = "";
    }
  };

  const disconnectGmail = async () => {
    if (!window.confirm("Remove the saved Gmail token and turn off automatic sync? You can revoke Job-Agent from your Google Account connections too.")) return;
    setSaving(true);
    try {
      await api("/api/actions/email-disconnect", { method: "POST", body: JSON.stringify({}) });
      await Promise.all([loadEmailStatus(), loadEmailReview()]);
      notify("Gmail disconnected on this device.");
    } catch (error) {
      notify(error.message, "error");
    } finally {
      setSaving(false);
    }
  };

  const changeAutoSync = async (enabled) => {
    setEmailStatus((previous) => ({ ...previous, auto_sync: enabled }));
    try {
      await api("/api/email/auto-sync", { method: "POST", body: JSON.stringify({ enabled }) });
      notify(enabled ? "Automatic Gmail sync enabled." : "Automatic Gmail sync disabled.");
    } catch (error) {
      notify(error.message, "error");
    } finally {
      await loadEmailStatus();
    }
  };

  const reviewEmail = async (item, action) => {
    const selectedJobId = reviewJobByMessage[item.message_id] || item.job_id;
    if (action === "approve" && !selectedJobId) {
      notify("Choose the job this email belongs to.", "error");
      return;
    }
    try {
      await api("/api/email/review", {
        method: "POST",
        body: JSON.stringify({
          message_id: item.message_id,
          action,
          ...(action === "approve" ? { job_id: Number(selectedJobId) } : {}),
        }),
      });
      notify(action === "approve" ? "Application status updated from email." : "Email dismissed from review.");
      await Promise.all([loadJobs(), loadEmailStatus(), loadEmailReview()]);
    } catch (error) {
      notify(error.message, "error");
    }
  };

  const filteredJobs = jobs.filter((job) => {
    const corpus = [job.id, job.company, job.title, job.location, job.url, job.notes, job.resume_used].filter(Boolean).join(" ").toLowerCase();
    return corpus.includes(searchText.trim().toLowerCase()) && (statusFilter === "all" || job.status === statusFilter);
  });
  const inProgress = jobs.filter((job) => ["discovered", "shortlisted", "ready"].includes(job.status)).length;
  const appliedCount = jobs.filter((job) => job.status === "applied").length;
  const interviewCount = jobs.filter((job) => job.status === "interview").length;
  const followUps = jobs.filter((job) => job.next_action && job.next_action <= todayISO() && !["rejected", "withdrawn"].includes(job.status)).length;

  if (window.location.pathname === "/gmail-setup.html") return <GmailSetupPage />;

  return (
    <main className="min-h-screen bg-slate-50 text-slate-900">
      <div className="mx-auto max-w-[1440px] px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
        <header className="mb-7 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-3">
            <div className="grid size-11 place-items-center rounded-xl bg-slate-950 text-white shadow-sm"><BriefcaseBusiness size={22} /></div>
            <div>
              <h1 className="text-xl font-bold tracking-tight text-slate-950">Job-Agent</h1>
              <p className="mt-0.5 text-sm text-slate-500">A clear view of your job search, all stored locally.</p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Pill tone="green"><span className="size-1.5 rounded-full bg-emerald-500" /> Local database</Pill>
            <Button onClick={() => { void refreshDashboard(); notify("Refreshing tracker…"); }}><RefreshCw size={15} /> Refresh</Button>
          </div>
        </header>

        <section aria-label="Application summary" className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">
          {[
            { label: "Total applications", value: jobs.length, icon: BriefcaseBusiness, color: "bg-blue-50 text-blue-700" },
            { label: "In progress", value: inProgress, icon: Clock3, color: "bg-amber-50 text-amber-700" },
            { label: "Applied", value: appliedCount, icon: CheckCircle2, color: "bg-emerald-50 text-emerald-700" },
            { label: "Interviews", value: interviewCount, icon: UserRound, color: "bg-violet-50 text-violet-700" },
            { label: "Follow-ups due", value: followUps, icon: CalendarClock, color: "bg-rose-50 text-rose-700" },
          ].map((item) => {
            const Icon = item.icon;
            return <article key={item.label} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-semibold text-slate-500 sm:text-sm">{item.label}</span>
                <span className={"grid size-8 shrink-0 place-items-center rounded-lg " + item.color}><Icon size={16} /></span>
              </div>
              <strong className="mt-3 block text-3xl font-bold tracking-tight text-slate-950 tabular-nums">{item.value}</strong>
            </article>;
          })}
        </section>

        <section className="mb-6 rounded-xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
          <div className="grid gap-4 lg:grid-cols-[minmax(230px,0.75fr)_minmax(0,1.8fr)] lg:items-center">
            <div>
              <div className="flex items-center gap-2"><span className="grid size-9 place-items-center rounded-lg bg-blue-50 text-blue-700"><Plus size={18} /></span><h2 className="font-semibold text-slate-900">Add a job</h2></div>
              <p className="mt-2 text-sm leading-5 text-slate-500">Paste a job posting URL to create a tracker entry.</p>
              <button type="button" className="mt-3 text-sm font-semibold text-blue-700 hover:text-blue-800" onClick={() => openEdit()}>Or add manually <span aria-hidden="true">→</span></button>
            </div>
            <form onSubmit={onAddJob} className="min-w-0">
              <label htmlFor="job-url" className="mb-1.5 block text-xs font-semibold text-slate-600">Job posting URL</label>
              <div className="flex flex-col gap-2 sm:flex-row">
                <input id="job-url" type="url" required value={jobUrl} onChange={(event) => setJobUrl(event.target.value)} placeholder="https://careers.company.com/jobs/123" className="min-h-11 min-w-0 flex-1 rounded-lg border border-slate-200 px-3.5 text-sm shadow-sm outline-none transition placeholder:text-slate-400 focus:border-blue-500 focus:ring-4 focus:ring-blue-50" />
                <Button type="submit" variant="primary"><Plus size={16} /> Add from URL</Button>
              </div>
              <p className="mt-2 text-xs text-slate-400">Job-Agent parses the URL and stores the job in your local SQLite database.</p>
            </form>
          </div>
        </section>

        <section className="mb-6 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
          <div className="flex flex-col gap-4 border-b border-slate-100 p-4 sm:flex-row sm:items-start sm:justify-between sm:p-5">
            <div className="flex items-start gap-3">
              <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-rose-50 text-rose-700"><Mail size={19} /></span>
              <div>
                <h2 className="flex flex-wrap items-center gap-2 font-semibold text-slate-900">Gmail integration
                  <Pill tone={emailStatus.connected ? "green" : emailStatus.available ? "slate" : "amber"}>{emailStatus.connected ? "Connected" : emailStatus.available ? "Not connected" : "Setup needed"}</Pill>
                </h2>
                <p className="mt-1 max-w-2xl text-sm leading-5 text-slate-500">Detect application confirmations, interviews, rejections and offers using read-only Gmail access.</p>
              </div>
            </div>
            <div className="flex flex-wrap gap-2 sm:justify-end">
              {!emailStatus.connected && <Button variant="primary" onClick={() => startTask("/api/actions/email-connect", {}, "Connect Gmail")}><Mail size={15} /> Connect Gmail</Button>}
              <Button disabled={!emailStatus.connected || !emailStatus.available} onClick={() => startTask("/api/actions/email-sync", {}, "Sync Gmail")}><RefreshCw size={15} /> Sync now</Button>
              {emailStatus.connected && <Button disabled={saving} onClick={disconnectGmail}><X size={15} /> Disconnect</Button>}
            </div>
          </div>
          <div className="grid gap-4 p-4 sm:grid-cols-3 sm:p-5">
            <div><p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Account</p><p className="mt-1 break-words text-sm font-semibold text-slate-800">{emailStatus.account || "Not connected"}</p></div>
            <div><p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Last sync</p><p className="mt-1 text-sm font-semibold text-slate-800">{formatDateTime(emailStatus.last_sync_at)}</p></div>
            <label className="flex items-start gap-3 rounded-lg bg-slate-50 p-3">
              <input type="checkbox" checked={Boolean(emailStatus.auto_sync)} disabled={!emailStatus.connected || !emailStatus.available} onChange={(event) => changeAutoSync(event.target.checked)} className="mt-1 size-4 accent-blue-700" />
              <span><strong className="block text-sm font-semibold text-slate-800">Automatic sync</strong><small className="mt-1 block text-xs leading-5 text-slate-500">Check every 15 minutes while the dashboard is running.</small></span>
            </label>
          </div>
          {!emailStatus.connected && (
            <div className="mx-4 mb-4 flex flex-col gap-2 rounded-lg border border-blue-100 bg-blue-50/60 p-3 sm:mx-5 sm:mb-5 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-xs leading-5 text-slate-600">Install the Gmail extra, enable the Gmail API, create a Desktop OAuth client, and save it as <code className="rounded bg-white px-1 py-0.5 text-slate-700">config/gmail-credentials.json</code>.</p>
              <a href="/gmail-setup.html" className="inline-flex shrink-0 items-center gap-1 text-sm font-semibold text-blue-700 hover:text-blue-900">Setup guide <ExternalLink size={14} /></a>
            </div>
          )}
          <p className="border-t border-slate-100 px-4 py-3 text-xs leading-5 text-slate-400 sm:px-5">Messages are read only. Only message metadata and a short preview are stored locally; full message bodies are processed in memory. Uncertain matches wait for your review.</p>
        </section>

        <section className="mb-6">
          <PanelHeading title="Email review queue" count={reviewItems.length} description="Review uncertain matches before they change an application status." action={<Button size="small" onClick={async () => { await Promise.all([loadEmailStatus(), loadEmailReview()]); notify("Email queue refreshed."); }}><RefreshCw size={14} /> Refresh queue</Button>} />
          {reviewItems.length === 0 ? (
            <div className="rounded-xl border border-slate-200 bg-white"><EmptyState icon={Mail} title="No emails need review" description="Ambiguous or unmatched application emails will show here after a Gmail sync." /></div>
          ) : (
            <div className="space-y-3">
              {reviewItems.map((item) => (
                <article key={item.message_id} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                    <div className="min-w-0">
                      <h3 className="break-words text-sm font-semibold text-slate-900">{item.subject || "(No subject)"}</h3>
                      <p className="mt-1 break-words text-xs leading-5 text-slate-500">{item.sender || "Unknown sender"} <span className="px-1 text-slate-300">·</span> {formatDateTime(item.received_at)}</p>
                    </div>
                    {item.detected_status && <StatusBadge status={item.detected_status} />}
                  </div>
                  {item.summary && <p className="mt-3 break-words text-sm leading-6 text-slate-600">{item.summary}</p>}
                  <p className="mt-2 text-xs leading-5 text-slate-500">{Math.round(Number(item.confidence || 0) * 100)}% match confidence <span className="px-1 text-slate-300">·</span> {item.reason || "This email needs confirmation."}</p>
                  <div className="mt-4 flex flex-col gap-3 border-t border-slate-100 pt-4 md:flex-row md:items-end md:justify-between">
                    <label className="block min-w-0 flex-1 text-xs font-semibold text-slate-600">Match to job
                      <select value={reviewJobByMessage[item.message_id] ?? String(item.job_id || "")} onChange={(event) => setReviewJobByMessage((previous) => ({ ...previous, [item.message_id]: event.target.value }))} className="mt-1.5 min-h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm font-normal text-slate-800 outline-none focus:border-blue-500 focus:ring-4 focus:ring-blue-50">
                        <option value="">Choose a tracked job…</option>
                        {reviewJobs.map((job) => <option key={job.id} value={String(job.id)}>{"#" + job.id + " · " + (job.company || "Unknown company") + " — " + (job.title || "Untitled role")}</option>)}
                      </select>
                    </label>
                    <div className="flex flex-wrap gap-2">
                      {item.thread_id && <a href={"https://mail.google.com/mail/u/0/#all/" + encodeURIComponent(item.thread_id)} target="_blank" rel="noreferrer" className="inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-slate-200 px-3 text-xs font-semibold text-slate-600 hover:bg-slate-50">Open email <ExternalLink size={13} /></a>}
                      <Button size="small" variant="primary" disabled={!item.detected_status} onClick={() => reviewEmail(item, "approve")}><Check size={14} /> Approve update</Button>
                      <Button size="small" onClick={() => reviewEmail(item, "dismiss")}><X size={14} /> Dismiss</Button>
                    </div>
                  </div>
                </article>
              ))}
            </div>
          )}
        </section>

        <section className="mb-6">
          <PanelHeading title="Applications" count={jobs.length} description="Track each role, update status, and keep follow-ups visible." action={<div className="flex flex-wrap gap-2">
            <input ref={fileInputRef} type="file" accept=".csv,text/csv" className="hidden" onChange={importCsv} />
            <Button onClick={() => fileInputRef.current?.click()}><ArrowUpFromLine size={15} /> Import CSV</Button>
            <Button onClick={exportCsv}><ArrowDownToLine size={15} /> Export CSV</Button>
          </div>} />
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-xs text-slate-400">
            <span className="inline-flex items-center gap-1.5 text-emerald-700"><span className="size-1.5 rounded-full bg-emerald-500" /> Synced with local database{lastSyncedAt ? " · " + new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(lastSyncedAt) : ""}</span>
            <span>CSV import merges by job URL and creates a backup.</span>
          </div>
          <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
            <div className="flex flex-col gap-3 border-b border-slate-100 p-3 sm:flex-row sm:items-center">
              <label className="relative min-w-0 flex-1">
                <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                <input type="search" value={searchText} onChange={(event) => setSearchText(event.target.value)} placeholder="Search company, role or location…" aria-label="Search applications" className="min-h-10 w-full rounded-lg border border-slate-200 py-2 pl-9 pr-3 text-sm outline-none placeholder:text-slate-400 focus:border-blue-500 focus:ring-4 focus:ring-blue-50" />
              </label>
              <label className="flex items-center gap-2 text-xs font-semibold text-slate-500"><Filter size={14} /> Status
                <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)} aria-label="Filter by status" className="min-h-10 rounded-lg border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 outline-none focus:border-blue-500 focus:ring-4 focus:ring-blue-50">
                  <option value="all">All statuses</option>
                  {STATUSES.map((status) => <option key={status.value} value={status.value}>{status.label}</option>)}
                </select>
              </label>
            </div>
            {jobs.length === 0 ? (
              <EmptyState title="No applications yet" description="Add a job URL above to begin tracking your applications." />
            ) : filteredJobs.length === 0 ? (
              <EmptyState icon={Search} title="No matching applications" description="Try another search phrase or status filter." />
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[1050px] border-collapse text-left">
                  <thead className="bg-slate-50 text-[10px] font-bold uppercase tracking-[0.1em] text-slate-500">
                    <tr>
                      <th className="px-4 py-3">ID</th>
                      <th className="px-4 py-3">Company / role</th>
                      <th className="px-4 py-3">Applied on</th>
                      <th className="px-4 py-3">Status</th>
                      <th className="px-4 py-3">Follow-up</th>
                      <th className="px-4 py-3">Resume</th>
                      <th className="px-4 py-3 text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100 text-sm">
                    {filteredJobs.map((job) => (
                      <tr key={job.id} className="align-middle transition hover:bg-slate-50/70">
                        <td className="px-4 py-4 font-mono text-xs text-slate-400">{"#" + job.id}</td>
                        <td className="max-w-[290px] px-4 py-4">
                          <p className="break-words font-semibold text-slate-900">{job.company || "Unknown company"}</p>
                          <p className="mt-0.5 break-words text-xs text-slate-500">{job.title || "Untitled role"}</p>
                          {job.location && <p className="mt-1 flex items-center gap-1 text-xs text-slate-400"><MapPin size={11} /> {job.location}</p>}
                          {job.url && <a href={job.url} target="_blank" rel="noreferrer" className="mt-1 inline-flex items-center gap-1 text-xs font-medium text-blue-700 hover:underline">Open posting <ExternalLink size={11} /></a>}
                        </td>
                        <td className="whitespace-nowrap px-4 py-4 text-xs text-slate-600">{formatDate(job.applied_date)}</td>
                        <td className="px-4 py-4">
                          <select aria-label={"Status for " + job.company + " — " + job.title} value={job.status || "discovered"} onChange={(event) => updateStatus(job, event.target.value)} className={"max-w-40 rounded-lg border px-2.5 py-2 text-xs font-semibold outline-none focus:ring-4 focus:ring-blue-50 " + (BADGE_CLASSES[job.status] ? "border-transparent " : "border-slate-200 bg-white text-slate-700") + (job.status === "applied" ? " bg-emerald-50 text-emerald-700" : job.status === "interview" ? " bg-violet-50 text-violet-700" : job.status === "rejected" ? " bg-rose-50 text-rose-700" : job.status === "offer" ? " bg-teal-50 text-teal-700" : job.status === "shortlisted" || job.status === "ready" ? " bg-indigo-50 text-indigo-700" : job.status === "discovered" ? " bg-amber-50 text-amber-700" : " bg-slate-100 text-slate-700")}>
                            {STATUSES.map((status) => <option key={status.value} value={status.value}>{status.label}</option>)}
                          </select>
                        </td>
                        <td className="whitespace-nowrap px-4 py-4 text-xs text-slate-600">
                          {formatDate(job.next_action)}
                          {job.next_action && job.next_action < todayISO() && !["rejected", "withdrawn"].includes(job.status) && <span className="mt-1 block text-[10px] font-semibold text-rose-600">Overdue</span>}
                        </td>
                        <td className="max-w-36 break-words px-4 py-4 text-xs text-slate-500">{job.resume_used || "—"}</td>
                        <td className="px-4 py-4">
                          <div className="flex justify-end gap-1.5">
                            <Button size="small" onClick={() => openEdit(job)}>Edit</Button>
                            <Button size="small" variant="primary" onClick={() => openAssist(job)}><Sparkles size={13} /> Assist</Button>
                            <Button size="small" variant="danger" onClick={() => deleteJob(job)} aria-label={"Delete " + job.company}><Trash2 size={13} /></Button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </section>

        {currentTask && (
          <section className="mb-6 rounded-xl border border-slate-200 bg-white shadow-sm">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-4 py-3.5 sm:px-5">
              <div className="flex items-center gap-2.5">
                <span className="grid size-8 place-items-center rounded-lg bg-slate-100 text-slate-600"><Activity size={16} /></span>
                <div><h2 className="text-sm font-semibold text-slate-900">Command activity</h2><p className="text-xs text-slate-500">{currentTask.label || "Job-Agent task"} · {currentTask.id}</p></div>
              </div>
              <Pill tone={currentTask.status === "succeeded" ? "green" : currentTask.status === "failed" ? "red" : "amber"}>{currentTask.status === "running" ? <><LoaderCircle size={12} className="animate-spin" /> Running</> : currentTask.status === "succeeded" ? "Completed" : "Failed"}</Pill>
            </div>
            <pre className="max-h-72 min-h-20 overflow-auto whitespace-pre-wrap break-words bg-slate-950 px-4 py-4 text-xs leading-6 text-slate-100 sm:px-5">{currentTask.output || "Waiting for command output…"}</pre>
            <p className="px-4 py-3 text-xs leading-5 text-slate-500 sm:px-5">If a task requests input, respond in the terminal running Job-Agent. Applications are never submitted automatically.</p>
          </section>
        )}

        <footer className="flex flex-col gap-2 border-t border-slate-200 py-5 text-xs text-slate-400 sm:flex-row sm:items-center sm:justify-between">
          <span>Job-Agent · Local-first application tracking</span>
          <span className="inline-flex items-center gap-1.5"><ShieldCheck size={13} /> SQLite · Local browser state · Backups before CSV import</span>
        </footer>
      </div>

      {editing && (
        <Modal
          title={editing.id ? "Edit application" : "Add application manually"}
          description={editing.id ? "Update details and follow-up information for this role." : "Create a tracker entry without parsing the job page."}
          onClose={() => setEditing(null)}
          footer={<><Button onClick={() => setEditing(null)}>Cancel</Button><Button variant="primary" disabled={saving} onClick={() => document.getElementById("application-form-submit").click()}>{saving ? <LoaderCircle size={15} className="animate-spin" /> : <Check size={15} />} Save application</Button></>}
          wide
        >
          <form id="application-form" onSubmit={saveEdit} className="grid gap-4 sm:grid-cols-2">
            <TextField label="Company" value={editForm.company} onChange={(value) => setEditForm((previous) => ({ ...previous, company: value }))} required maxLength={200} />
            <TextField label="Role" value={editForm.title} onChange={(value) => setEditForm((previous) => ({ ...previous, title: value }))} required maxLength={300} />
            <TextField label="Job URL" value={editForm.url} onChange={(value) => setEditForm((previous) => ({ ...previous, url: value }))} type="url" required placeholder="https://…" wide />
            <TextField label="Location" value={editForm.location} onChange={(value) => setEditForm((previous) => ({ ...previous, location: value }))} maxLength={200} />
            <SelectField label="Status" value={editForm.status} onChange={(value) => setEditForm((previous) => ({ ...previous, status: value }))}>{STATUSES.map((status) => <option key={status.value} value={status.value}>{status.label}</option>)}</SelectField>
            <TextField label="Applied on" value={editForm.applied_date} onChange={(value) => setEditForm((previous) => ({ ...previous, applied_date: value }))} type="date" />
            <TextField label="Next follow-up" value={editForm.next_action} onChange={(value) => setEditForm((previous) => ({ ...previous, next_action: value }))} type="date" />
            <TextField label="Resume used" value={editForm.resume_used} onChange={(value) => setEditForm((previous) => ({ ...previous, resume_used: value }))} maxLength={250} placeholder="e.g. ai-engineer.pdf" wide />
            <TextField label="Notes" value={editForm.notes} onChange={(value) => setEditForm((previous) => ({ ...previous, notes: value }))} rows={3} maxLength={4000} placeholder="Recruiter, interview details, follow-up context…" wide />
            <button id="application-form-submit" type="submit" className="hidden" />
          </form>
        </Modal>
      )}

      {assistJob && (
        <Modal
          title="Prepare your application"
          description={"Job #" + assistJob.id + ": " + (assistJob.title || "Untitled role") + " — " + (assistJob.company || "Unknown company")}
          onClose={() => setAssistJob(null)}
          footer={<><Button onClick={() => setAssistJob(null)}>Cancel</Button><Button variant="primary" onClick={() => document.getElementById("assist-form-submit").click()} disabled={!selectedResume}><Sparkles size={15} /> Run autofill</Button></>}
        >
          <form id="assist-form" onSubmit={beginApply} className="space-y-5">
            <SelectField label="Resume file" value={selectedResume} onChange={setSelectedResume} disabled={resumes.length === 0}>
              {resumes.length === 0 ? <option value="">No resumes found</option> : resumes.map((resume) => <option key={resume} value={resume}>{resume}</option>)}
            </SelectField>
            {resumes.length === 0 && <p className="text-xs leading-5 text-amber-700">Add a PDF, DOC or DOCX resume to the project’s resumes/ directory, then refresh the page.</p>}
            <SelectField label="Browser to use" value={browserMode} onChange={setBrowserMode}>
              <option value="chrome">Google Chrome (persistent login)</option>
              <option value="managed">Managed Playwright browser</option>
            </SelectField>
            {browserMode === "chrome" ? (
              <div className="rounded-lg border border-blue-100 bg-blue-50 p-3 text-xs leading-5 text-blue-900">
                Start <code className="rounded bg-white px-1">job-agent browser</code> in a terminal first. Sign in to your Google account and career portals in that dedicated Chrome window. Job-Agent will open the application in a new tab.
              </div>
            ) : (
              <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs leading-5 text-slate-600">
                Uses Job-Agent’s separate Playwright profile. Log in when prompted; sessions persist in that profile.
              </div>
            )}
            <p className="text-xs leading-5 text-slate-500">Review the completed form and submit it yourself. Unknown application questions may need manual answers.</p>
            <button id="assist-form-submit" type="submit" className="hidden" />
          </form>
        </Modal>
      )}

      {toastMessage && (
        <div role="status" className={"fixed bottom-4 right-4 z-[70] flex max-w-sm items-start gap-2.5 rounded-xl border px-4 py-3 text-sm shadow-xl " + (toastMessage.kind === "error" ? "border-rose-200 bg-white text-rose-800" : "border-slate-200 bg-slate-950 text-white")}>
          {toastMessage.kind === "error" ? <CircleAlert size={17} className="mt-0.5 shrink-0" /> : <CheckCircle2 size={17} className="mt-0.5 shrink-0 text-emerald-400" />}
          <span className="leading-5">{toastMessage.message}</span>
          <button type="button" onClick={() => setToastMessage(null)} aria-label="Dismiss notification" className="ml-1 text-current opacity-70 hover:opacity-100"><X size={15} /></button>
        </div>
      )}
    </main>
  );
}
