'use client';

import { useEffect, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { Activity, AlertTriangle, Bell, BriefcaseBusiness, Check, ChevronDown, CircleHelp, Database, ExternalLink, FileSearch, FolderKanban, Gauge, KeyRound, LayoutDashboard, Link2, Map, Menu, Network, RefreshCw, Rss, Search, Settings, ShieldCheck, Sparkles, UserRound, Users, X } from 'lucide-react';

type View = 'Dashboard' | 'Cases' | 'Entities' | 'Graph' | 'Timeline' | 'Map' | 'Evidence' | 'News' | 'AI Analyst' | 'Search' | 'Admin';
type Entity = { id: string; name: string; type: string; confidence: number; case_id?: string };
type Case = { id: string; title: string; description: string; status: string; priority: string };
type Relationship = { id: string; source: string; target: string; type: string; confidence: number; case_id?: string };
type NewsSource = { id: string; name: string; url: string; source_type: string; description: string; enabled: boolean; public_access: boolean; last_synced_at?: string | null };
type NewsItem = { id: string; source_id: string; source_name: string; title: string; url: string; summary: string; author: string; published_at?: string | null; discovered_at: string; case_id?: string | null; review_status: string };
type Data = { stats: { active_cases: number; entities: number; relationships: number; evidence_ingestion: number; alerts: number }; cases: Case[]; entities: Entity[]; relationships: Relationship[]; activity: string[]; alerts: string[]; tasks: string[]; newsItems: NewsItem[]; newsSources: NewsSource[] };
type AiStatus = { zero_cost_mode: boolean; active_provider: string; configured_provider: string; model: string; context_limit: number; ollama: { available: boolean; models: string[]; error: string | null }; openai: { enabled_by_admin: boolean; enabled_by_environment: boolean; api_key_configured: boolean; available: boolean; spending_limit_usd: number }; chatgpt_subscription: { status: string; message: string }; usage: { local_requests: number; external_requests: number; estimated_cost_usd: number; provider_errors: number } };
const API = process.env.NEXT_PUBLIC_API_BASE_URL || '/api';
const REQUEST_TIMEOUT_MS = 10000;
const nav: { label: View; icon: typeof Activity }[] = [{ label: 'Dashboard', icon: LayoutDashboard }, { label: 'Cases', icon: BriefcaseBusiness }, { label: 'Entities', icon: Users }, { label: 'Graph', icon: Network }, { label: 'Timeline', icon: Activity }, { label: 'Map', icon: Map }, { label: 'Evidence', icon: Database }, { label: 'News', icon: Rss }, { label: 'AI Analyst', icon: Sparkles }, { label: 'Search', icon: Search }, { label: 'Admin', icon: Settings }];

async function fetchWithTimeout(input: RequestInfo | URL, init: RequestInit = {}, timeoutMs = REQUEST_TIMEOUT_MS) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(input, { ...init, signal: controller.signal });
  } finally {
    window.clearTimeout(timeout);
  }
}

async function fetchWorkspace(): Promise<Data> {
  const dashboardResponse = await fetchWithTimeout(`${API}/dashboard`, { credentials: 'include' });
  if (!dashboardResponse.ok) throw new Error('Dashboard data could not be loaded.');
  const [newsItemsResponse, newsSourcesResponse] = await Promise.all([
    fetchWithTimeout(`${API}/news/items`, { credentials: 'include' }),
    fetchWithTimeout(`${API}/news/sources`, { credentials: 'include' }),
  ]);
  const dashboard = await dashboardResponse.json();
  return { ...dashboard, newsItems: newsItemsResponse.ok ? await newsItemsResponse.json() : [], newsSources: newsSourcesResponse.ok ? await newsSourcesResponse.json() : [] };
}

export default function Workspace() {
  const [view, setView] = useState<View>('Dashboard');
  const [mobileOpen, setMobileOpen] = useState(false);
  const [loggedIn, setLoggedIn] = useState(false);
  const [role, setRole] = useState('analyst');
  const [mustChangePassword, setMustChangePassword] = useState(false);
  const [loading, setLoading] = useState(true);
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const meResponse = await fetchWithTimeout(`${API}/auth/me`, { credentials: 'include' });
        if (!meResponse.ok) return;
        const me = await meResponse.json();
        if (me.must_change_password) {
          if (cancelled) return;
          setLoggedIn(true);
          setRole(me.role || 'analyst');
          setMustChangePassword(true);
          return;
        }
        const workspace = await fetchWorkspace();
        if (cancelled) return;
        setLoggedIn(true);
        setRole(me.role || 'analyst');
        setData(workspace);
      } catch {
        if (!cancelled) setError('The Argus API is unavailable.');
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => { cancelled = true; };
  }, []);

  async function login(username: string, password: string) {
    setError('');
    const response = await fetchWithTimeout(`${API}/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include', body: JSON.stringify({ username, password }) });
    if (!response.ok) { setError('Invalid credentials or API unavailable.'); return; }
    const body = await response.json();
    setRole(body.user?.role || 'analyst');
    setMustChangePassword(Boolean(body.user?.must_change_password));
    setLoggedIn(true);
    if (body.user?.must_change_password) return;
    let workspace: Data;
    try { workspace = await fetchWorkspace(); } catch { setError('Signed in, but workspace data could not be loaded.'); return; }
    setData(workspace);
  }

  async function finishPasswordChange() {
    try {
      setData(await fetchWorkspace());
      setMustChangePassword(false);
    } catch { setError('Password changed, but workspace data could not be loaded.'); }
  }

  if (loading) return <div className="grid min-h-screen place-items-center bg-ink text-sm text-slate-400">Connecting to Argus…</div>;
  if (mustChangePassword) return <PasswordChange onChanged={finishPasswordChange} />;
  if (!loggedIn || !data) return <Login onLogin={login} error={error} />;
  const filtered = data.entities.filter((entity) => `${entity.id} ${entity.name} ${entity.type}`.toLowerCase().includes(query.toLowerCase()));

  async function refreshWorkspace() { try { setData(await fetchWorkspace()); } catch { setError('Workspace refresh failed.'); } }

  return <div className="min-h-screen bg-ink text-slate-100">
    <aside className={`${mobileOpen ? 'translate-x-0' : '-translate-x-full'} fixed inset-y-0 left-0 z-30 flex w-[220px] flex-col border-r border-line bg-[#0d151d] transition-transform md:translate-x-0`}>
      <div className="flex h-[72px] items-center gap-3 border-b border-line px-5"><div className="grid h-8 w-8 place-items-center rounded-lg border border-cyan/50 text-cyan"><Network size={19}/></div><span className="text-lg font-semibold tracking-tight">Argus</span><button className="ml-auto md:hidden" onClick={() => setMobileOpen(false)} aria-label="Close navigation"><X size={18}/></button></div>
      <div className="flex-1 px-3 py-4">{nav.map(({ label, icon: Icon }) => <button key={label} onClick={() => { setView(label); setMobileOpen(false); }} className={`mb-1 flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition ${view === label ? 'bg-cyan/15 text-cyan' : 'text-slate-400 hover:bg-slate-800/60 hover:text-slate-100'} ${label === 'Admin' ? 'mt-7 border-t border-line pt-5' : ''}`}><Icon size={18} strokeWidth={1.8}/><span>{label}</span>{label === 'Admin' && <ShieldCheck size={14} className="ml-auto text-violet"/>}</button>)}</div>
      <div className="border-t border-line p-4 text-[11px] text-slate-500"><div className="mb-2 flex items-center gap-2 text-mint"><span className="h-1.5 w-1.5 rounded-full bg-mint pulse"/>System operational</div><div>{role} · private deployment</div></div>
    </aside>
    <div className="md:pl-[220px]"><header className="sticky top-0 z-20 flex h-[72px] items-center gap-3 border-b border-line bg-[#0b1117]/95 px-4 backdrop-blur md:px-7"><button className="md:hidden" onClick={() => setMobileOpen(true)} aria-label="Open navigation"><Menu size={20}/></button><div className="relative max-w-[670px] flex-1"><Search size={17} className="absolute left-3 top-3 text-slate-500"/><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search Argus (cases, entities, evidence, events…)" className="h-10 w-full rounded-lg border border-line bg-panel pl-10 pr-16 text-sm text-slate-200 outline-none ring-cyan/30 placeholder:text-slate-500 focus:ring-2"/><span className="absolute right-3 top-2.5 hidden rounded border border-line px-1.5 py-0.5 text-[11px] text-slate-500 sm:block">⌘ K</span></div><button className="relative rounded-lg p-2 text-slate-400 hover:bg-panel hover:text-white"><Bell size={18}/><span className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-amber"/></button><button className="rounded-lg p-2 text-slate-400 hover:bg-panel hover:text-white"><CircleHelp size={18}/></button><div className="hidden h-7 w-px bg-line md:block"/><button className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm text-slate-200 hover:bg-panel"><span className="grid h-7 w-7 place-items-center rounded-full bg-violet/20 text-violet"><UserRound size={16}/></span><span className="hidden sm:block">Analyst</span><ChevronDown size={14} className="text-slate-500"/></button></header>
      <main className="mx-auto max-w-[1540px] p-4 md:p-7"><div className="mb-6 flex flex-wrap items-end justify-between gap-4"><div><div className="mb-2 flex items-center gap-2 text-xs text-mint"><span className="h-2 w-2 rounded-full bg-mint pulse"/>SYSTEM OPERATIONAL</div><h1 className="text-2xl font-semibold tracking-tight md:text-[28px]">{view === 'Dashboard' ? 'Command Center' : view}</h1><p className="mt-1 text-sm text-slate-400">{view === 'Dashboard' ? 'Intelligence analysis and investigation operations' : `Authorized ${view.toLowerCase()} workspace`}</p></div><div className="flex items-center gap-3 text-xs text-slate-500"><span>Last updated · 14:37 UTC</span><button className="rounded-md border border-line bg-panel px-3 py-2 text-slate-300 hover:border-cyan/50">Customize view</button></div></div>{view === 'Dashboard' ? <Dashboard data={data} filtered={filtered}/> : <SecondaryView view={view} entities={filtered} relationships={data.relationships} cases={data.cases} newsItems={data.newsItems} newsSources={data.newsSources} role={role} setView={setView} refresh={refreshWorkspace}/>}</main></div>
  </div>;
}

function Login({ onLogin, error }: { onLogin: (username: string, password: string) => Promise<void>; error: string }) { const [username, setUsername] = useState('analyst'); const [password, setPassword] = useState('change-me-locally'); return <div className="grid min-h-screen place-items-center bg-ink px-4"><div className="w-full max-w-md rounded-2xl border border-line bg-panel/90 p-8 shadow-2xl"><div className="mb-8 flex items-center gap-3"><div className="grid h-10 w-10 place-items-center rounded-xl border border-cyan/50 text-cyan"><Network size={21}/></div><div><div className="text-xl font-semibold">Argus</div><div className="text-xs text-slate-500">Authorized intelligence workspace</div></div></div><h1 className="text-2xl font-semibold">Sign in</h1><p className="mt-2 text-sm text-slate-400">Access is controlled by the service, not the browser.</p><form className="mt-6 space-y-4" onSubmit={(event) => { event.preventDefault(); void onLogin(username, password); }}><label className="block text-xs text-slate-400">Username<input value={username} onChange={(event) => setUsername(event.target.value)} className="mt-2 h-11 w-full rounded-lg border border-line bg-[#0c141c] px-3 text-sm outline-none focus:border-cyan" /></label><label className="block text-xs text-slate-400">Password<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} className="mt-2 h-11 w-full rounded-lg border border-line bg-[#0c141c] px-3 text-sm outline-none focus:border-cyan" /></label>{error && <div className="rounded-lg border border-amber/30 bg-amber/10 p-3 text-xs text-amber">{error}</div>}<button className="h-11 w-full rounded-lg bg-cyan font-medium text-ink hover:bg-mint">Enter workspace</button></form><div className="mt-6 flex items-center gap-2 text-[11px] text-slate-500"><ShieldCheck size={14} className="text-mint"/>Synthetic demo data only</div></div></div>; }

function PasswordChange({ onChanged }: { onChanged: () => Promise<void> }) {
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [notice, setNotice] = useState('');
  const [saving, setSaving] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setNotice('');
    if (newPassword.length < 12) { setNotice('Use at least 12 characters.'); return; }
    if (newPassword !== confirmation) { setNotice('New passwords do not match.'); return; }
    setSaving(true);
    try {
      const response = await fetchWithTimeout(`${API}/auth/change-password`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include', body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }) });
      if (!response.ok) { const body = await response.json().catch(() => ({})); setNotice(body.detail || 'Password change failed.'); return; }
      await onChanged();
    } catch { setNotice('The Argus API is unavailable.'); }
    finally { setSaving(false); }
  }
  return <div className="grid min-h-screen place-items-center bg-ink px-4"><div className="w-full max-w-md rounded-2xl border border-line bg-panel/90 p-8 shadow-2xl"><div className="mb-8 flex items-center gap-3"><div className="grid h-10 w-10 place-items-center rounded-xl border border-cyan/50 text-cyan"><KeyRound size={21}/></div><div><div className="text-xl font-semibold">Set a new password</div><div className="text-xs text-slate-500">Your temporary password must be replaced before continuing.</div></div></div><form className="space-y-4" onSubmit={submit}><label className="block text-xs text-slate-400">Temporary password<input required type="password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} className="mt-2 h-11 w-full rounded-lg border border-line bg-[#0c141c] px-3 text-sm outline-none focus:border-cyan" /></label><label className="block text-xs text-slate-400">New password<input required minLength={12} type="password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} className="mt-2 h-11 w-full rounded-lg border border-line bg-[#0c141c] px-3 text-sm outline-none focus:border-cyan" /></label><label className="block text-xs text-slate-400">Confirm new password<input required minLength={12} type="password" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} className="mt-2 h-11 w-full rounded-lg border border-line bg-[#0c141c] px-3 text-sm outline-none focus:border-cyan" /></label>{notice && <div className="rounded-lg border border-amber/30 bg-amber/10 p-3 text-xs text-amber">{notice}</div>}<button disabled={saving} className="h-11 w-full rounded-lg bg-cyan font-medium text-ink hover:bg-mint disabled:opacity-50">{saving ? 'Saving…' : 'Change password'}</button></form></div></div>;
}

function Panel({ title, action, children, className = '' }: { title: string; action?: string; children: ReactNode; className?: string }) { return <section className={`overflow-hidden rounded-xl border border-line bg-panel/90 ${className}`}><div className="flex items-center justify-between border-b border-line px-4 py-3"><h2 className="text-sm font-semibold text-slate-100">{title}</h2>{action && <span className="text-xs text-cyan">{action} →</span>}</div>{children}</section>; }
function Stat({ icon: Icon, label, value, color }: { icon: typeof Activity; label: string; value: string; color: string }) { return <div className="rounded-xl border border-line bg-panel/90 p-4"><Icon size={20} className={color}/><div className="mt-4 text-xs text-slate-400">{label}</div><div className="mt-1 text-[25px] font-semibold tracking-tight">{value}</div></div>; }
function Dashboard({ data, filtered }: { data: Data; filtered: Entity[] }) { return <><section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5"><Stat icon={BriefcaseBusiness} label="Active Cases" value={String(data.stats.active_cases)} color="text-violet"/><Stat icon={Users} label="Entities" value={data.stats.entities.toLocaleString()} color="text-cyan"/><Stat icon={Link2} label="Relationships" value={data.stats.relationships.toLocaleString()} color="text-violet"/><Stat icon={Database} label="Evidence Ingestion" value={`${data.stats.evidence_ingestion}%`} color="text-mint"/><Stat icon={AlertTriangle} label="Alerts" value={String(data.stats.alerts)} color="text-amber"/></section><section className="mt-3 grid gap-3 xl:grid-cols-[minmax(0,1.65fr)_minmax(340px,.95fr)]"><GraphPanel data={data} filtered={filtered}/><Panel title="Case Overview"><div className="p-4"><div className="flex items-center gap-2 text-sm font-medium"><FolderKanban size={17} className="text-violet"/>{data.cases[0]?.id} — {data.cases[0]?.title}</div><p className="mt-4 text-sm leading-6 text-slate-400">{data.cases[0]?.description}</p><div className="mt-4 h-2 rounded-full bg-slate-800"><div className="h-2 w-[68%] rounded-full bg-mint"/></div><div className="mt-2 text-right text-xs text-slate-400">68% investigation progress</div></div></Panel></section><section className="mt-3 grid gap-3 xl:grid-cols-3"><Panel title="Recent Activity"><div className="divide-y divide-line">{data.activity.map((item) => <div className="flex gap-3 px-4 py-3 text-xs text-slate-300" key={item}><Activity size={15} className="text-cyan"/>{item}</div>)}</div></Panel><Panel title="Evidence Ingestion"><div className="p-4"><div className="text-3xl font-semibold text-mint">96%</div><div className="mt-3 h-2 rounded-full bg-slate-800"><div className="h-2 w-[96%] rounded-full bg-mint"/></div><div className="mt-4 space-y-3 text-xs text-slate-400">{['Communications — Complete','Transactions — Complete','Registry Data — Complete'].map((item) => <div className="flex gap-2" key={item}><Check size={14} className="text-mint"/>{item}</div>)}</div></div></Panel><Panel title="Analyst Task Queue"><div className="divide-y divide-line">{data.tasks.map((item) => <div className="flex gap-3 px-4 py-3 text-xs text-slate-300" key={item}><span className="h-4 w-4 rounded border border-slate-600"/>{item}</div>)}</div></Panel></section></>; }
function GraphPanel({ data, filtered }: { data: Data; filtered: Entity[] }) { const points = [{ id: 'E-18472', x: 32, y: 32, c: '#4fd1c5' }, { id: 'E-18473', x: 50, y: 50, c: '#54e2d7' }, { id: 'E-18474', x: 22, y: 72, c: '#9b83ff' }, { id: 'E-18475', x: 72, y: 67, c: '#9b83ff' }, { id: 'E-18476', x: 73, y: 30, c: '#4fd1c5' }]; return <Panel title="Relationship Graph · Case View" action="Open explorer" className="min-h-[420px]"><div className="relative grid-fade h-[370px] overflow-hidden bg-[#0b1219]"><svg viewBox="0 0 100 100" preserveAspectRatio="none" className="absolute inset-0 h-full w-full">{data.relationships.map((link) => { const source = points.find((point) => point.id === link.source); const target = points.find((point) => point.id === link.target); return source && target ? <line key={link.id} x1={source.x} y1={source.y} x2={target.x} y2={target.y} stroke="#53d5dc" strokeWidth=".45"/> : null; })}</svg>{points.map((point) => <div key={point.id} style={{ left: `${point.x}%`, top: `${point.y}%`, borderColor: point.c }} className="absolute grid h-10 w-10 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full border-2 bg-[#111b25] shadow-[0_0_20px_rgba(79,209,197,.2)]"><span className="h-2.5 w-2.5 rounded-full" style={{ background: point.c }}/></div>)}</div><div className="flex items-center justify-between border-t border-line px-4 py-2 text-xs text-slate-500"><span>{filtered.length} visible entities · {data.relationships.length} relationships</span><span className="flex items-center gap-1.5"><Sparkles size={13} className="text-violet"/> synthetic projection</span></div></Panel>; }

function SecondaryView({ view, entities, relationships, cases, newsItems, newsSources, role, setView, refresh }: { view: View; entities: Entity[]; relationships: Relationship[]; cases: Case[]; newsItems: NewsItem[]; newsSources: NewsSource[]; role: string; setView: (view: View) => void; refresh: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [caseId, setCaseId] = useState(cases[0]?.id || '');
  const [entityType, setEntityType] = useState('Person');
  const [confidence, setConfidence] = useState('70');
  const [notice, setNotice] = useState('');

  async function submit(event: FormEvent) {
    event.preventDefault();
    const isEntity = view === 'Entities';
    const payload = isEntity ? { name, entity_type: entityType, description, confidence: Number(confidence), case_id: caseId } : { title: name, description, priority: 'medium' };
    const response = await fetchWithTimeout(`${API}/${isEntity ? 'entities' : 'cases'}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include', body: JSON.stringify(payload) });
    if (!response.ok) { setNotice('Could not save. Check your authorization and fields.'); return; }
    setOpen(false);
    setName('');
    setDescription('');
    setNotice(`${isEntity ? 'Entity' : 'Case'} created. Refreshing…`);
    window.location.reload();
  }

  if (view === 'Graph') return <GraphView entities={entities} relationships={relationships} cases={cases} setView={setView} refresh={refresh}/>;
  if (view === 'AI Analyst') return <AiWorkspace cases={cases}/>;
  if (view === 'Admin') return <AdminView cases={cases} role={role}/>;
  if (view === 'News') return <NewsView items={newsItems} sources={newsSources} cases={cases} refresh={refresh}/>;

  const rows = view === 'Entities' ? entities.map((entity) => ({ id: entity.id, name: entity.name, type: entity.type, confidence: entity.confidence })) : cases.map((item) => ({ id: item.id, name: item.title, type: `${item.status} case`, confidence: item.priority === 'high' ? 80 : 60 }));
  return <div className="grid gap-4 lg:grid-cols-[1.4fr_.9fr]"><Panel title={`${view} registry`} action="Export authorized view"><div className="flex items-center justify-between border-b border-line px-4 py-3"><span className="text-xs text-slate-500">{rows.length} authorized records</span>{(view === 'Cases' || view === 'Entities') && <button onClick={() => { setNotice(''); setOpen(true); }} className="rounded-md bg-cyan px-3 py-2 text-xs font-medium text-ink hover:bg-mint">New {view === 'Entities' ? 'entity' : 'case'}</button>}</div><div className="divide-y divide-line">{rows.map((row) => <div key={row.id} className="flex items-center gap-4 px-4 py-4"><div className="grid h-9 w-9 place-items-center rounded-lg bg-violet/10 text-violet">{view === 'Entities' ? <Users size={17}/> : <FolderKanban size={17}/>}</div><div className="min-w-0 flex-1"><div className="truncate text-sm text-slate-200">{row.name}</div><div className="mt-1 text-xs text-slate-500">{row.id} · {row.type}</div></div><div className="text-xs text-slate-400">{row.confidence}% confidence</div></div>)}</div></Panel><Panel title="Workspace controls"><div className="space-y-4 p-4 text-sm text-slate-300"><button className="flex w-full items-center gap-3 rounded-lg border border-line bg-[#0f1821] p-3 text-left hover:border-cyan/40"><FileSearch size={17} className="text-cyan"/>Filter by access classification</button><button className="flex w-full items-center gap-3 rounded-lg border border-line bg-[#0f1821] p-3 text-left hover:border-cyan/40"><Gauge size={17} className="text-violet"/>Adjust confidence threshold</button><button className="flex w-full items-center gap-3 rounded-lg border border-line bg-[#0f1821] p-3 text-left hover:border-cyan/40"><KeyRound size={17} className="text-amber"/>Review authorization scope</button>{notice && <div className="rounded-lg border border-cyan/20 bg-cyan/5 p-3 text-xs leading-5 text-slate-400">{notice}</div>}</div></Panel>{open && <div className="fixed inset-0 z-40 grid place-items-center bg-black/60 p-4"><form onSubmit={submit} className="w-full max-w-lg rounded-xl border border-line bg-panel p-6 shadow-2xl"><div className="flex items-center justify-between"><h2 className="text-lg font-semibold">Create {view === 'Entities' ? 'entity' : 'case'}</h2><button type="button" onClick={() => setOpen(false)} className="text-slate-400"><X size={18}/></button></div><div className="mt-5 space-y-4"><label className="block text-xs text-slate-400">{view === 'Entities' ? 'Name' : 'Title'}<input required value={name} onChange={(event) => setName(event.target.value)} className="mt-2 h-10 w-full rounded-lg border border-line bg-[#0c141c] px-3 text-sm text-slate-200 outline-none focus:border-cyan"/></label><label className="block text-xs text-slate-400">Description<textarea value={description} onChange={(event) => setDescription(event.target.value)} className="mt-2 min-h-24 w-full rounded-lg border border-line bg-[#0c141c] px-3 py-2 text-sm text-slate-200 outline-none focus:border-cyan"/></label>{view === 'Entities' && <><label className="block text-xs text-slate-400">Entity type<select value={entityType} onChange={(event) => setEntityType(event.target.value)} className="mt-2 h-10 w-full rounded-lg border border-line bg-[#0c141c] px-3 text-sm text-slate-200"><option>Person</option><option>Organization</option><option>Location</option><option>Asset</option><option>Device</option><option>Website</option></select></label><label className="block text-xs text-slate-400">Case<select value={caseId} onChange={(event) => setCaseId(event.target.value)} className="mt-2 h-10 w-full rounded-lg border border-line bg-[#0c141c] px-3 text-sm text-slate-200">{cases.map((item) => <option key={item.id} value={item.id}>{item.id} · {item.title}</option>)}</select></label><label className="block text-xs text-slate-400">Confidence<input type="number" min="0" max="100" value={confidence} onChange={(event) => setConfidence(event.target.value)} className="mt-2 h-10 w-full rounded-lg border border-line bg-[#0c141c] px-3 text-sm text-slate-200"/></label></>}</div><button className="mt-6 h-10 w-full rounded-lg bg-cyan font-medium text-ink hover:bg-mint">Save to authorized workspace</button></form></div>}</div>;
}

function AdminView({ cases, role }: { cases: Case[]; role: string }) {
  return <div className="space-y-4"><OllamaSettingsPanel role={role}/><AdminViewContent cases={cases} role={role}/></div>;
}

function OllamaSettingsPanel({ role }: { role: string }) {
  const [models, setModels] = useState<string[]>([]);
  const [selectedModel, setSelectedModel] = useState('');
  const [available, setAvailable] = useState(false);
  const [notice, setNotice] = useState('');
  const isAdmin = role === 'administrator' || role === 'super_administrator';

  async function load() {
    const response = await fetchWithTimeout(`${API}/${isAdmin ? 'admin/ai/settings' : 'ai/status'}`, { credentials: 'include' });
    if (!response.ok) return;
    const body = await response.json() as AiStatus;
    setModels(body.ollama.models);
    setAvailable(body.ollama.available);
    setSelectedModel(body.ollama.models.includes(body.model) ? body.model : body.ollama.models[0] || '');
  }

  useEffect(() => { void load(); }, [isAdmin]);

  async function save() {
    if (!isAdmin || !selectedModel) return;
    const response = await fetchWithTimeout(`${API}/admin/ai/settings`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, credentials: 'include', body: JSON.stringify({ active_provider: 'ollama', ollama_model: selectedModel }) });
    setNotice(response.ok ? 'Ollama model selected.' : 'Only an administrator can change Ollama settings.');
    if (response.ok) await load();
  }

  return <Panel title="Ollama provider"><div className="grid gap-3 p-4 md:grid-cols-[1fr_auto_auto] md:items-end"><label className="block text-xs text-slate-400">Discovered model<select value={selectedModel} onChange={(event) => setSelectedModel(event.target.value)} className="mt-2 h-10 w-full rounded-lg border border-line bg-[#0c141c] px-3 text-sm text-slate-200"><option value="">{available ? 'No models discovered' : 'Connection unavailable'}</option>{models.map((model) => <option key={model} value={model}>{model}</option>)}</select></label><button onClick={() => void load()} className="h-10 rounded-lg border border-line px-3 text-xs text-slate-300 hover:border-cyan/40">Test connection</button><button onClick={() => void save()} disabled={!isAdmin || !selectedModel} className="h-10 rounded-lg bg-cyan px-3 text-xs font-medium text-ink hover:bg-mint disabled:opacity-50">Save model</button>{notice && <div className="text-xs text-slate-400 md:col-span-3">{notice}</div>}</div></Panel>;
}

function AdminViewContent({ cases, role }: { cases: Case[]; role: string }) {
  const [status, setStatus] = useState<AiStatus | null>(null);
  const [selectedModel, setSelectedModel] = useState('');
  const [question, setQuestion] = useState('Summarize the authorized evidence in this case.');
  const [caseId, setCaseId] = useState(cases[0]?.id || '');
  const [answer, setAnswer] = useState('');
  const [notice, setNotice] = useState('');
  const isAdmin = role === 'administrator' || role === 'super_administrator';

  async function loadStatus() {
    const response = await fetchWithTimeout(`${API}/${isAdmin ? 'admin/ai/settings' : 'ai/status'}`, { credentials: 'include' });
    if (!response.ok) { setNotice('AI status is unavailable for this role.'); return; }
    const nextStatus = await response.json() as AiStatus;
    setStatus(nextStatus);
    setSelectedModel(nextStatus.ollama.models.includes(nextStatus.model) ? nextStatus.model : nextStatus.ollama.models[0] || '');
  }

  useEffect(() => { void loadStatus(); }, [isAdmin]);

  async function toggleZeroCost() {
    if (!isAdmin || !status) return;
    const response = await fetchWithTimeout(`${API}/admin/ai/settings`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, credentials: 'include', body: JSON.stringify({ zero_cost_mode: !status.zero_cost_mode }) });
    if (!response.ok) { setNotice('Only an administrator can change the cost safety mode.'); return; }
    setStatus(await response.json());
  }

  async function selectOllama() {
    if (!isAdmin || !selectedModel) return;
    const response = await fetchWithTimeout(`${API}/admin/ai/settings`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, credentials: 'include', body: JSON.stringify({ active_provider: 'ollama', ollama_model: selectedModel }) });
    if (!response.ok) { setNotice('Ollama settings could not be saved.'); return; }
    setStatus(await response.json());
    setNotice('Ollama is now the selected zero-cost provider.');
  }

  async function ask(event: FormEvent) {
    event.preventDefault();
    setNotice('');
    const response = await fetchWithTimeout(`${API}/ai/question`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include', body: JSON.stringify({ question, case_id: caseId || null, analysis_type: 'question' }) }, 90000);
    if (!response.ok) { setNotice('The authorized analysis request failed.'); return; }
    const result = await response.json();
    setAnswer(`${result.answer} Provider: ${result.engine || result.provider_used}. ${result.fallback_reason || ''}`);
  }

  return <div className="space-y-4"><div className="flex flex-wrap items-end justify-between gap-3"><div><h2 className="text-lg font-semibold">AI Cost &amp; Usage</h2><p className="mt-1 text-sm text-slate-400">Every provider decision is visible. Paid inference is never an automatic fallback.</p></div><button onClick={() => void loadStatus()} className="rounded-md border border-line px-3 py-2 text-xs text-slate-300 hover:border-cyan/40">Refresh status</button></div>{status && <><section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4"><Stat icon={ShieldCheck} label="Zero Additional Cost Mode" value={status.zero_cost_mode ? 'ON' : 'OFF'} color={status.zero_cost_mode ? 'text-mint' : 'text-amber'}/><Stat icon={Sparkles} label="Active Provider" value={status.active_provider} color="text-cyan"/><Stat icon={Activity} label="Local Requests" value={String(status.usage.local_requests)} color="text-violet"/><Stat icon={AlertTriangle} label="Estimated Cost" value={`$${status.usage.estimated_cost_usd.toFixed(4)}`} color="text-amber"/></section><div className="grid gap-4 lg:grid-cols-2"><Panel title="Provider availability"><div className="space-y-3 p-4 text-sm"><div className="flex items-center justify-between"><span className="text-slate-400">Current model</span><span className="text-slate-100">{status.model}</span></div><div className="flex items-center justify-between"><span className="text-slate-400">Ollama</span><span className={status.ollama.available ? 'text-mint' : 'text-amber'}>{status.ollama.available ? `Available · ${status.ollama.models.length} model(s)` : 'Unavailable · deterministic fallback'}</span></div><div className="flex items-center justify-between"><span className="text-slate-400">OpenAI API</span><span className={status.openai.available ? 'text-amber' : 'text-slate-500'}>{status.openai.available ? 'Manually enabled' : 'Disabled'}</span></div><div className="flex items-center justify-between"><span className="text-slate-400">ChatGPT plan</span><span className="text-slate-500">Approval required for remote VPS</span></div><div className="border-t border-line pt-3 text-xs leading-5 text-slate-500">{status.chatgpt_subscription.message}</div></div></Panel><Panel title="Usage controls"><div className="space-y-3 p-4 text-sm"><div className="flex items-center justify-between"><span className="text-slate-400">External requests this month</span><span>{status.usage.external_requests}</span></div><div className="flex items-center justify-between"><span className="text-slate-400">Provider errors</span><span>{status.usage.provider_errors}</span></div><div className="flex items-center justify-between"><span className="text-slate-400">Configured OpenAI limit</span><span>${status.openai.spending_limit_usd.toFixed(2)}</span></div>{isAdmin ? <button onClick={() => void toggleZeroCost()} className="mt-2 h-10 w-full rounded-lg border border-cyan/30 text-cyan hover:bg-cyan/10">{status.zero_cost_mode ? 'Keep paid providers blocked' : 'Enable Zero Additional Cost Mode'}</button> : <div className="rounded-lg border border-line bg-[#0f1821] p-3 text-xs text-slate-500">Only an administrator can change provider safety settings.</div>}</div></Panel></div><Panel title="Ask Argus without paid inference"><form onSubmit={ask} className="space-y-3 p-4"><textarea value={question} onChange={(event) => setQuestion(event.target.value)} className="min-h-24 w-full rounded-lg border border-line bg-[#0c141c] px-3 py-2 text-sm text-slate-200 outline-none focus:border-cyan"/><select value={caseId} onChange={(event) => setCaseId(event.target.value)} className="h-10 w-full rounded-lg border border-line bg-[#0c141c] px-3 text-sm text-slate-200"><option value="">No case selected</option>{cases.map((item) => <option key={item.id} value={item.id}>{item.id} · {item.title}</option>)}</select><button className="h-10 rounded-lg bg-cyan px-4 text-sm font-medium text-ink hover:bg-mint">Run authorized analysis</button>{answer && <div className="rounded-lg border border-mint/20 bg-mint/5 p-3 text-sm leading-6 text-slate-300">{answer}</div>}{notice && <div className="rounded-lg border border-amber/20 bg-amber/5 p-3 text-xs text-amber">{notice}</div>}</form></Panel></>}</div>;
}

function AiWorkspace({ cases }: { cases: Case[] }) {
  const [status, setStatus] = useState<AiStatus | null>(null);
  const [caseId, setCaseId] = useState(cases[0]?.id || '');
  const [question, setQuestion] = useState('What does the authorized case evidence show?');
  const [answer, setAnswer] = useState('');
  const [running, setRunning] = useState(false);
  const [notice, setNotice] = useState('');

  useEffect(() => { void fetchWithTimeout(`${API}/ai/status`, { credentials: 'include' }).then(async (response) => { if (response.ok) setStatus(await response.json()); }); }, []);

  async function ask(event: FormEvent) {
    event.preventDefault();
    setRunning(true);
    setAnswer('');
    setNotice('');
    const response = await fetchWithTimeout(`${API}/ai/question/stream`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include', body: JSON.stringify({ question, case_id: caseId || null, analysis_type: 'question' }) }, 90000);
    if (!response.ok || !response.body) { setRunning(false); setNotice('The AI provider is unavailable; try again or use deterministic analysis.'); return; }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';
      for (const line of lines) {
        if (!line.startsWith('data: ') || line === 'data: [DONE]') continue;
        try {
          const eventData = JSON.parse(line.slice(6)) as { type?: string; text?: string; result?: { fallback_reason?: string } };
          if (eventData.type === 'answer_delta') setAnswer((current) => current + (eventData.text || ''));
          if (eventData.type === 'complete' && eventData.result?.fallback_reason) setNotice(eventData.result.fallback_reason);
        } catch { /* Ignore partial SSE frames. */ }
      }
    }
    setRunning(false);
  }

  return <div className="space-y-4"><div><h2 className="text-lg font-semibold">AI Intelligence Workspace</h2><p className="mt-1 text-sm text-slate-400">Questions stay inside your authorization scope. Local deterministic analysis continues when Ollama is unavailable.</p></div><div className="grid gap-4 lg:grid-cols-[1.5fr_.8fr]"><Panel title="Ask Argus"><form onSubmit={ask} className="space-y-4 p-4"><textarea value={question} onChange={(event) => setQuestion(event.target.value)} className="min-h-32 w-full rounded-lg border border-line bg-[#0c141c] px-3 py-2 text-sm text-slate-200 outline-none focus:border-cyan"/><select value={caseId} onChange={(event) => setCaseId(event.target.value)} className="h-10 w-full rounded-lg border border-line bg-[#0c141c] px-3 text-sm text-slate-200"><option value="">No case selected</option>{cases.map((item) => <option key={item.id} value={item.id}>{item.id} · {item.title}</option>)}</select><button disabled={running} className="h-10 rounded-lg bg-cyan px-4 text-sm font-medium text-ink hover:bg-mint disabled:opacity-50">{running ? 'Streaming response…' : 'Ask securely'}</button>{answer && <div className="rounded-lg border border-cyan/20 bg-cyan/5 p-4 text-sm leading-6 text-slate-300">{answer}</div>}{notice && <div className="rounded-lg border border-amber/20 bg-amber/5 p-3 text-xs text-amber">{notice}</div>}</form></Panel><Panel title="Current routing"><div className="space-y-3 p-4 text-sm"><div className="flex items-center justify-between"><span className="text-slate-400">Provider</span><span className="text-cyan">{status?.active_provider || 'Checking…'}</span></div><div className="flex items-center justify-between"><span className="text-slate-400">Model</span><span>{status?.model || '—'}</span></div><div className="flex items-center justify-between"><span className="text-slate-400">Zero-cost mode</span><span className="text-mint">{status?.zero_cost_mode ? 'Enabled' : 'Disabled'}</span></div><div className="border-t border-line pt-3 text-xs leading-5 text-slate-500">If Cloudron Ollama is unavailable, Argus returns a deterministic authorized result instead of switching to a paid service.</div></div></Panel></div></div>;
}

function GraphView({ entities, relationships, cases, setView, refresh }: { entities: Entity[]; relationships: Relationship[]; cases: Case[]; setView: (view: View) => void; refresh: () => Promise<void> }) {
  const [sourceId, setSourceId] = useState(entities[0]?.id || '');
  const [targetId, setTargetId] = useState(entities[1]?.id || '');
  const [relationshipType, setRelationshipType] = useState('Associated With');
  const [confidence, setConfidence] = useState('70');
  const [notice, setNotice] = useState('');
  const [saving, setSaving] = useState(false);
  const caseId = cases[0]?.id || '';
  const entityNames = new globalThis.Map(entities.map((entity) => [entity.id, entity.name]));

  async function createRelationship(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setNotice('');
    const response = await fetchWithTimeout(`${API}/relationships`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include', body: JSON.stringify({ source_id: sourceId, target_id: targetId, relationship_type: relationshipType, confidence: Number(confidence), case_id: caseId }) });
    setSaving(false);
    if (!response.ok) { setNotice('Relationship could not be saved. Both entities must belong to the selected authorized case.'); return; }
    setNotice('Relationship saved to the authorized case.');
    await refresh();
  }

  return <div className="space-y-4"><div className="flex items-center justify-between"><div><h2 className="text-lg font-semibold">Graph Explorer</h2><p className="mt-1 text-sm text-slate-400">Create and review case-scoped relationships. Every edge stays inside the authorized case.</p></div><button onClick={() => setView('Dashboard')} className="rounded-md border border-line px-3 py-2 text-xs text-slate-300">Back to dashboard</button></div><div className="grid gap-4 lg:grid-cols-[1.2fr_.8fr]"><Panel title="Authorized relationships"><div className="divide-y divide-line">{relationships.length === 0 ? <div className="p-6 text-sm text-slate-500">No relationships in the authorized workspace yet.</div> : relationships.map((relationship) => <div key={relationship.id} className="flex items-center gap-3 px-4 py-4"><div className="grid h-9 w-9 place-items-center rounded-lg bg-cyan/10 text-cyan"><Link2 size={17}/></div><div className="min-w-0 flex-1"><div className="truncate text-sm text-slate-200">{entityNames.get(relationship.source) || relationship.source} <span className="text-cyan">→</span> {entityNames.get(relationship.target) || relationship.target}</div><div className="mt-1 text-xs text-slate-500">{relationship.type} · {relationship.id} · {relationship.confidence}% confidence</div></div><span className="rounded border border-mint/20 px-2 py-1 text-[11px] text-mint">{relationship.case_id}</span></div>)}</div></Panel><Panel title="Add relationship"><form onSubmit={createRelationship} className="space-y-4 p-4"><label className="block text-xs text-slate-400">Source entity<select required value={sourceId} onChange={(event) => setSourceId(event.target.value)} className="mt-2 h-10 w-full rounded-lg border border-line bg-[#0c141c] px-3 text-sm text-slate-200">{entities.map((entity) => <option key={entity.id} value={entity.id}>{entity.name} · {entity.id}</option>)}</select></label><label className="block text-xs text-slate-400">Target entity<select required value={targetId} onChange={(event) => setTargetId(event.target.value)} className="mt-2 h-10 w-full rounded-lg border border-line bg-[#0c141c] px-3 text-sm text-slate-200">{entities.map((entity) => <option key={entity.id} value={entity.id}>{entity.name} · {entity.id}</option>)}</select></label><label className="block text-xs text-slate-400">Relationship type<select value={relationshipType} onChange={(event) => setRelationshipType(event.target.value)} className="mt-2 h-10 w-full rounded-lg border border-line bg-[#0c141c] px-3 text-sm text-slate-200"><option>Associated With</option><option>Employed By</option><option>Owns</option><option>Located At</option><option>Participated In</option><option>Communicated With</option><option>Connected To</option><option>Referenced In</option></select></label><label className="block text-xs text-slate-400">Confidence<input type="number" min="0" max="100" value={confidence} onChange={(event) => setConfidence(event.target.value)} className="mt-2 h-10 w-full rounded-lg border border-line bg-[#0c141c] px-3 text-sm text-slate-200"/></label><button disabled={saving || entities.length < 2 || !caseId} className="h-10 w-full rounded-lg bg-cyan font-medium text-ink hover:bg-mint disabled:opacity-50">{saving ? 'Saving…' : 'Save relationship'}</button>{notice && <div className="rounded-lg border border-cyan/20 bg-cyan/5 p-3 text-xs leading-5 text-slate-300">{notice}</div>}</form></Panel></div></div>;
}

function NewsView({ items, sources, cases, refresh }: { items: NewsItem[]; sources: NewsSource[]; cases: Case[]; refresh: () => Promise<void> }) {
  const [syncing, setSyncing] = useState('');
  const [notice, setNotice] = useState('');
  const [selectedCase, setSelectedCase] = useState(cases[0]?.id || '');

  async function sync(sourceId: string) {
    setSyncing(sourceId);
    setNotice('');
    const response = await fetch(`${API}/news/sources/${sourceId}/sync`, { method: 'POST', credentials: 'include' });
    setSyncing('');
    if (!response.ok) { setNotice('The source could not be synchronized.'); return; }
    const result = await response.json();
    setNotice(`${result.items_created} new items imported; ${result.items_skipped} duplicates skipped.`);
    await refresh();
  }

  async function attach(itemId: string) {
    if (!selectedCase) return;
    const response = await fetch(`${API}/news/items/${itemId}/attach`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'include', body: JSON.stringify({ case_id: selectedCase }) });
    if (!response.ok) { setNotice('The news item could not be attached to that case.'); return; }
    setNotice('News item attached to the authorized case.');
    await refresh();
  }

  return <div className="space-y-4"><div className="grid gap-4 lg:grid-cols-[1.1fr_1.9fr]"><Panel title="Connected news sources"><div className="divide-y divide-line">{sources.map((source) => <div key={source.id} className="flex items-center gap-3 px-4 py-4"><div className="grid h-9 w-9 place-items-center rounded-lg bg-cyan/10 text-cyan"><Rss size={17}/></div><div className="min-w-0 flex-1"><div className="truncate text-sm text-slate-200">{source.name}</div><div className="mt-1 truncate text-xs text-slate-500">{source.description}</div><div className="mt-1 text-[11px] text-slate-600">{source.last_synced_at ? `Last sync ${new Date(source.last_synced_at).toLocaleString()}` : 'Not synchronized yet'}</div></div><button onClick={() => void sync(source.id)} disabled={syncing === source.id} className="flex items-center gap-2 rounded-md border border-line px-3 py-2 text-xs text-slate-300 hover:border-cyan/50 disabled:opacity-50"><RefreshCw size={13} className={syncing === source.id ? 'animate-spin' : ''}/>{syncing === source.id ? 'Syncing' : 'Sync now'}</button></div>)}</div></Panel><Panel title="Authorized news workflow"><div className="space-y-3 p-4 text-sm text-slate-400"><p>Imported headlines retain source URLs, publication time, discovery time, and duplicate status.</p><label className="block text-xs text-slate-500">Attach new items to<select value={selectedCase} onChange={(event) => setSelectedCase(event.target.value)} className="mt-2 h-10 w-full rounded-lg border border-line bg-[#0c141c] px-3 text-sm text-slate-200">{cases.map((item) => <option key={item.id} value={item.id}>{item.id} · {item.title}</option>)}</select></label>{notice && <div className="rounded-lg border border-cyan/20 bg-cyan/5 p-3 text-xs leading-5 text-slate-300">{notice}</div>}</div></Panel></div><Panel title="Imported news"><div className="divide-y divide-line">{items.length === 0 ? <div className="p-6 text-sm text-slate-500">No news items imported yet. Use Sync now to fetch the configured public source.</div> : items.map((item) => <article key={item.id} className="px-4 py-4"><div className="flex flex-wrap items-start gap-3"><div className="min-w-0 flex-1"><div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.16em] text-cyan"><span>{item.source_name}</span><span className="text-slate-600">·</span><span>{item.published_at ? new Date(item.published_at).toLocaleString() : 'Date unavailable'}</span></div><a href={item.url} target="_blank" rel="noreferrer" className="mt-2 block text-sm font-medium text-slate-100 hover:text-cyan">{item.title} <ExternalLink size={13} className="ml-1 inline"/></a>{item.summary && <p className="mt-2 line-clamp-2 text-xs leading-5 text-slate-400">{item.summary}</p>}<div className="mt-2 text-[11px] text-slate-600">Discovered {new Date(item.discovered_at).toLocaleString()} · {item.review_status}</div></div>{item.case_id ? <span className="rounded border border-mint/30 px-2 py-1 text-[11px] text-mint">Attached · {item.case_id}</span> : <button onClick={() => void attach(item.id)} className="rounded-md border border-line px-3 py-2 text-xs text-slate-300 hover:border-cyan/50">Attach to case</button>}</div></article>)}</div></Panel></div>;
}
