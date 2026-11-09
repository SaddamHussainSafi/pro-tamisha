import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, has, hasCompanyWide, type AgingCustomer, type Customer, type CustomerGroup, type InvoiceSummary, type OrderSummary } from "../api";
import { useAuth } from "../auth";
import { cad, dec } from "../lib/decimal";
import { buildCreate, buildPatch, customerMessage, EMPTY_FORM, formFrom, formProblems, termsLabel, type CustomerForm } from "../lib/customers";
import { ORDER_STATUS } from "../lib/orders";
import { INVOICE_STATE } from "../lib/payments";
import { DataGrid, type Column } from "../ui/DataGrid";
import { CardGrid, EntityCard } from "../ui/CardGrid";
import { ViewToggle } from "../ui/ViewToggle";
import { useViewMode } from "../lib/viewMode";
import { Avatar, CardTitle, EmptyState, ErrorState, FilterBar, LoadingRows, PageHeader, StatusChip } from "../ui/primitives";

type Status = "active" | "inactive" | "all";
const STATUS_OPTIONS: { value: Status; label: string }[] = [
  { value: "active", label: "Active" },
  { value: "inactive", label: "Inactive" },
  { value: "all", label: "All" },
];
const money = (v: string) => cad(dec(v));
const day = (iso: string) =>
  new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
const link = "font-medium text-brand-primary hover:underline";

function canRead(s: NonNullable<ReturnType<typeof useAuth>["session"]>) {
  return has(s, "catalog.read") || has(s, "price.read");
}

function Notice({ tone, children }: { tone: "error" | "ok"; children: ReactNode }) {
  return tone === "error" ? (
    <p role="alert" className="alert-danger">
      {children}
    </p>
  ) : (
    <p role="status" className="alert-success">
      {children}
    </p>
  );
}

function NoAccess() {
  return (
    <div className="space-y-5">
      <PageHeader title="Customers" />
      <EmptyState title="You don't have access to customers" hint="Ask a manager if you need to see customer details." />
    </div>
  );
}

function ActiveChip({ active }: { active: boolean }) {
  return active ? <StatusChip tone="success">Active</StatusChip> : <StatusChip tone="neutral">Inactive</StatusChip>;
}

/** The editable fields, shared by "New customer" and the detail page. */
function CustomerFields({
  f,
  set,
  problems,
  groups,
  isNew,
}: {
  f: CustomerForm;
  set: (k: keyof CustomerForm, v: string | boolean) => void;
  problems: Partial<Record<keyof CustomerForm, string>>;
  groups: CustomerGroup[];
  isNew: boolean;
}) {
  const field = (k: keyof CustomerForm, label: string, input: ReactNode, hint?: string) => (
    <div>
      <label className="label" htmlFor={`cu-${k}`}>
        {label}
      </label>
      {input}
      {problems[k] ? (
        <p id={`cu-${k}-err`} className="mt-1 text-sm text-danger">
          {problems[k]}
        </p>
      ) : (
        hint && <p className="mt-1 text-sm text-ink-muted">{hint}</p>
      )}
    </div>
  );
  const aria = (k: keyof CustomerForm) => (problems[k] ? { "aria-invalid": true, "aria-describedby": `cu-${k}-err` } : {});
  return (
    <div className="grid gap-4 md:grid-cols-2">
      {field("name", "Name", <input id="cu-name" className="input" value={f.name} maxLength={200} required onChange={(e) => set("name", e.target.value)} {...aria("name")} />)}
      {isNew
        ? field(
            "ref",
            "Customer reference (optional)",
            <input id="cu-ref" className="input" value={f.ref} maxLength={64} onChange={(e) => set("ref", e.target.value)} {...aria("ref")} />,
            "The code used on price lists and order sheets, e.g. C-1003. It can't be changed later.",
          )
        : null}
      {field(
        "email",
        "Email for invoices and reminders",
        <input id="cu-email" type="email" className="input" value={f.email} maxLength={320} onChange={(e) => set("email", e.target.value)} {...aria("email")} />,
        "Leave empty if invoices are not emailed.",
      )}
      {field("phone", "Phone", <input id="cu-phone" type="tel" className="input" value={f.phone} maxLength={32} onChange={(e) => set("phone", e.target.value)} />)}
      {field(
        "group_id",
        "Customer group",
        <select id="cu-group_id" className="input" value={f.group_id} onChange={(e) => set("group_id", e.target.value)}>
          <option value="">No group</option>
          {groups.map((g) => (
            <option key={g.id} value={g.id}>
              {g.name} ({g.code})
            </option>
          ))}
        </select>,
        "Groups can have their own prices on the price list.",
      )}
      {field(
        "terms",
        "Payment terms (days)",
        <input id="cu-terms" className="input" inputMode="numeric" value={f.terms} maxLength={3} onChange={(e) => set("terms", e.target.value)} {...aria("terms")} />,
        "0 = due on receipt. Sets the due date on new invoices.",
      )}
      {!isNew && (
        <label className="flex items-center gap-2.5 text-sm md:col-span-2">
          <input type="checkbox" className="size-4 accent-brand-primary" checked={f.active} onChange={(e) => set("active", e.target.checked)} />
          Active customer
        </label>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- list */

export function CustomersPage() {
  const { session } = useAuth();
  const navigate = useNavigate();
  const [rows, setRows] = useState<Customer[] | null>(null);
  const [groups, setGroups] = useState<CustomerGroup[]>([]);
  const [loadError, setLoadError] = useState(false);
  const [status, setStatus] = useState<Status>("active");
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [group, setGroup] = useState("");
  const [creating, setCreating] = useState(false);
  const [f, setF] = useState<CustomerForm>(EMPTY_FORM);
  const [tried, setTried] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const allowed = !!session && canRead(session);
  const canManage = !!session && hasCompanyWide(session, "catalog.manage");
  const canAging = !!session && has(session, "invoice.read") && (has(session, "payment.record") || has(session, "payment.verify"));
  const [view, setView] = useViewMode("customers");
  const [aging, setAging] = useState<Record<string, AgingCustomer>>({});
  useEffect(() => {
    if (canAging) api.receivables().then((r) => setAging(Object.fromEntries(r.customers.map((x) => [x.customer_id, x]))), () => setAging({}));
  }, [canAging]);

  useEffect(() => {
    const t = setTimeout(() => setQ(search.trim()), 250);
    return () => clearTimeout(t);
  }, [search]);

  const loadGroups = useCallback(() => api.customerGroups().then(setGroups, () => setGroups([])), []);
  const load = useCallback(async () => {
    if (!allowed) return;
    try {
      setRows(
        await api.customers({
          q: q || undefined,
          group_id: group || undefined,
          active: status === "all" ? undefined : status === "active",
          limit: 500,
        }),
      );
      setLoadError(false);
    } catch {
      setLoadError(true);
    }
  }, [allowed, q, group, status]);
  useEffect(() => void load(), [load]);
  useEffect(() => {
    if (allowed) void loadGroups();
  }, [allowed, loadGroups]);

  if (!session) return null;
  if (!allowed) return <NoAccess />;

  const groupName = (id: string | null) => groups.find((g) => g.id === id)?.name ?? "—";
  const problems = tried ? formProblems(f, true) : {};

  async function create(e: FormEvent) {
    e.preventDefault();
    setTried(true);
    setError(null);
    if (Object.keys(formProblems(f, true)).length) return;
    setBusy(true);
    try {
      const c = await api.createCustomer(buildCreate(f));
      navigate(`/customers/${c.id}`, { state: { created: true } });
    } catch (err) {
      setError(customerMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const columns: Column<Customer>[] = [
    {
      key: "name",
      header: "Customer",
      cell: (c) => (
        <span className="flex items-center gap-3">
          <Avatar name={c.name} />
          <Link className={link} to={`/customers/${c.id}`}>
            {c.name}
          </Link>
        </span>
      ),
      sort: (c) => c.name.toLowerCase(),
    },
    {
      key: "ref",
      header: "Reference",
      cell: (c) => c.external_ref ?? "—",
      sort: (c) => c.external_ref ?? "",
    },
    {
      key: "group",
      header: "Group",
      cell: (c) => groupName(c.group_id),
      sort: (c) => groupName(c.group_id),
    },
    {
      key: "email",
      header: "Email",
      cell: (c) => c.email ?? <span className="text-ink-muted">No email</span>,
    },
    { key: "phone", header: "Phone", cell: (c) => c.phone ?? "—" },
    {
      key: "terms",
      header: "Terms",
      cell: (c) => termsLabel(c.payment_terms_days),
      sort: (c) => BigInt(c.payment_terms_days),
    },
    {
      key: "status",
      header: "Status",
      cell: (c) => <ActiveChip active={c.active} />,
    },
  ];

  return (
    <div className="space-y-5">
      <PageHeader
        title="Customers"
        subtitle="Who you sell to: contact details, payment terms and price group."
        actions={
          <>
            <ViewToggle value={view} onChange={setView} />
            {canManage && !creating && (
              <button type="button" className="btn-accent" onClick={() => setCreating(true)}>
                New customer
              </button>
            )}
          </>
        }
      />

      {creating && (
        <form className="card space-y-4 p-6" aria-labelledby="cu-new" onSubmit={create} noValidate>
          <h2 id="cu-new" className="text-xl font-semibold tracking-tight">
            New customer
          </h2>
          {error && <Notice tone="error">{error}</Notice>}
          <CustomerFields f={f} set={(k, v) => setF((p) => ({ ...p, [k]: v }))} problems={problems} groups={groups} isNew />
          <div className="flex justify-end gap-2">
            <button
              type="button"
              className="btn-ghost"
              onClick={() => {
                setCreating(false);
                setF(EMPTY_FORM);
                setTried(false);
                setError(null);
              }}
            >
              Cancel
            </button>
            <button className="btn-primary" disabled={busy}>
              Add customer
            </button>
          </div>
        </form>
      )}

      <FilterBar search={search} onSearch={setSearch} options={STATUS_OPTIONS} value={status} onChange={setStatus} placeholder="Search name or reference">
        {groups.length > 0 && (
          <label>
            <span className="sr-only">Filter by group</span>
            <select className="input w-48 rounded-full py-2" value={group} onChange={(e) => setGroup(e.target.value)}>
              <option value="">All groups</option>
              {groups.map((g) => (
                <option key={g.id} value={g.id}>
                  {g.name}
                </option>
              ))}
            </select>
          </label>
        )}
      </FilterBar>

      {loadError ? (
        <ErrorState title="Couldn't load customers" onRetry={() => void load()} />
      ) : rows === null ? (
        <LoadingRows cols={6} />
      ) : view === "grid" ? (
        <CardGrid
          items={rows}
          keyOf={(c) => c.id}
          caption={`Customers (${rows.length})`}
          empty={<EmptyState title={q || group ? "No customers match" : "No customers yet"} hint={q || group ? "Try another search or group." : undefined} />}
          render={(c) => {
            const owed = aging[c.id];
            const late = owed ? dec(owed.total) - dec(owed.current) : 0n;
            return (
              <EntityCard
                to={`/customers/${c.id}`}
                title={c.name}
                subtitle={[c.external_ref, groupName(c.group_id) !== "—" ? groupName(c.group_id) : null].filter(Boolean).join(" · ") || "No reference"}
                media={<Avatar name={c.name} />}
                dim={!c.active}
                facts={[
                  { label: "Terms", value: termsLabel(c.payment_terms_days) },
                  { label: "Email", value: c.email ?? <span className="text-ink-muted">No email</span> },
                  ...(c.phone ? [{ label: "Phone", value: c.phone }] : []),
                  ...(canAging
                    ? [
                        {
                          label: "Owes",
                          value: owed ? (
                            <span className={`num font-semibold ${late > 0n ? "text-danger" : ""}`}>
                              {cad(dec(owed.total))}
                              {late > 0n && ` · ${cad(late)} late`}
                            </span>
                          ) : (
                            <span className="text-ink-muted">Nothing</span>
                          ),
                        },
                      ]
                    : []),
                ]}
                chips={c.active ? undefined : <ActiveChip active={false} />}
              />
            );
          }}
        />
      ) : (
        <DataGrid
          rows={rows}
          columns={columns}
          rowKey={(c) => c.id}
          caption={`Customers (${rows.length})`}
          onOpen={(c) => navigate(`/customers/${c.id}`)}
          empty={<EmptyState title={q || group ? "No customers match" : "No customers yet"} hint={q || group ? "Try another search or group." : undefined} />}
        />
      )}

      <Groups groups={groups} canManage={canManage} onAdded={loadGroups} />
    </div>
  );
}

function Groups({ groups, canManage, onAdded }: { groups: CustomerGroup[]; canManage: boolean; onAdded: () => Promise<void> }) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [msg, setMsg] = useState<{ tone: "error" | "ok"; text: string } | null>(null);

  async function add(e: FormEvent) {
    e.preventDefault();
    setMsg(null);
    try {
      const g = await api.createCustomerGroup({
        code: code.trim().toUpperCase(),
        name: name.trim(),
      });
      setMsg({ tone: "ok", text: `Group ${g.name} added.` });
      setCode("");
      setName("");
      await onAdded();
    } catch (err) {
      setMsg({ tone: "error", text: customerMessage(err) });
    }
  }

  return (
    <section className="card space-y-4 p-6" aria-labelledby="cu-groups">
      <CardTitle id="cu-groups" title="Customer groups" />
      <p className="text-sm text-ink-muted">A group can have its own prices on the price list (group code column).</p>
      {groups.length ? (
        <ul className="flex flex-wrap gap-2" aria-label="Groups">
          {groups.map((g) => (
            <li key={g.id} className="chip border-line bg-subtle px-3.5 py-1.5 text-[13px] text-ink">
              {g.name} <span className="text-ink-muted">· {g.code}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-ink-muted">No groups yet.</p>
      )}
      {canManage && (
        <form className="grid gap-3 md:grid-cols-[12rem_1fr_auto] md:items-end" aria-label="Add a customer group" onSubmit={add}>
          <div>
            <label className="label" htmlFor="cg-code">
              Group code
            </label>
            <input id="cg-code" className="input uppercase" value={code} maxLength={32} required pattern="[A-Za-z0-9_\-]+" onChange={(e) => setCode(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="cg-name">
              Group name
            </label>
            <input id="cg-name" className="input" value={name} maxLength={200} required onChange={(e) => setName(e.target.value)} />
          </div>
          <button className="btn-ghost">Add group</button>
        </form>
      )}
      {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
    </section>
  );
}

/* ---------------------------------------------------------------- detail */

export function CustomerDetailPage() {
  const { id = "" } = useParams();
  const { session } = useAuth();
  const [c, setC] = useState<Customer | null>(null);
  const [groups, setGroups] = useState<CustomerGroup[]>([]);
  const [f, setF] = useState<CustomerForm>(EMPTY_FORM);
  const [loadError, setLoadError] = useState<string | null>(null);
  const created = (useLocation().state as { created?: boolean } | null)?.created;
  const [params] = useSearchParams();
  const [msg, setMsg] = useState<{ tone: "error" | "ok"; text: string } | null>(created ? { tone: "ok", text: "Customer added." } : null);
  const [tried, setTried] = useState(false);
  const [busy, setBusy] = useState(false);
  const allowed = !!session && canRead(session);
  const canManage = !!session && hasCompanyWide(session, "catalog.manage");

  const load = useCallback(async () => {
    if (!allowed) return;
    try {
      const x = await api.customer(id);
      setC(x);
      setF(formFrom(x));
      setLoadError(null);
    } catch (err) {
      setLoadError(customerMessage(err));
    }
  }, [allowed, id]);
  useEffect(() => void load(), [load]);
  useEffect(() => {
    if (allowed) api.customerGroups().then(setGroups, () => setGroups([]));
  }, [allowed]);

  if (!session) return null;
  if (!allowed) return <NoAccess />;
  if (loadError) return <ErrorState title="Couldn't load this customer" detail={loadError} onRetry={() => void load()} />;
  if (!c) return <LoadingRows cols={4} />;

  const tabs = [
    { key: "details", label: "Details" },
    ...(has(session, "invoice.read") ? [{ key: "invoices", label: "Invoices" }] : []),
    ...(has(session, "sales_order.read") ? [{ key: "orders", label: "Orders" }] : []),
  ];
  const want = params.get("tab") ?? "details";
  const tab = tabs.some((t) => t.key === want) ? want : "details";
  const patch = buildPatch(c, f);
  const problems = tried ? formProblems(f) : {};
  const group = groups.find((g) => g.id === c.group_id);

  async function save(e: FormEvent) {
    e.preventDefault();
    if (!c) return;
    setTried(true);
    setMsg(null);
    if (Object.keys(formProblems(f)).length || !patch) return;
    setBusy(true);
    try {
      const x = await api.updateCustomer(c.id, patch);
      setC(x);
      setF(formFrom(x));
      setTried(false);
      setMsg({ tone: "ok", text: "Customer saved." });
    } catch (err) {
      setMsg({ tone: "error", text: customerMessage(err) });
      await load();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <PageHeader
        title={c.name}
        subtitle={
          <span className="inline-flex flex-wrap items-center gap-2">
            {c.external_ref ?? "No reference"}
            {group && <span>· {group.name}</span>}
            <span>· {termsLabel(c.payment_terms_days)}</span>
            <ActiveChip active={c.active} />
          </span>
        }
        actions={
          <>
            {has(session, "report.read") && (
              <Link className="btn-ghost" to={`/reports?customer_id=${c.id}`}>
                Sales report
              </Link>
            )}
            <Link className="btn-ghost" to="/customers">
              All customers
            </Link>
          </>
        }
      />
      <nav aria-label="Customer sections" className="flex flex-wrap gap-1.5">
        {tabs.map((t) => (
          <Link
            key={t.key}
            to={t.key === "details" ? `/customers/${c.id}` : `/customers/${c.id}?tab=${t.key}`}
            aria-current={tab === t.key ? "page" : undefined}
            className={`chip px-4 py-2 text-sm ${tab === t.key ? "border-brand-secondary bg-brand-secondary text-white" : "border-line bg-card text-ink hover:bg-subtle"}`}
          >
            {t.label}
          </Link>
        ))}
      </nav>
      {tab === "invoices" && <CustomerInvoices customerId={c.id} />}
      {tab === "orders" && <CustomerOrders customerId={c.id} />}
      {tab === "details" && (
        <div className="grid gap-5 xl:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
          <section className="card space-y-4 p-6" aria-labelledby="cu-details">
            <CardTitle id="cu-details" title="Details" />
            {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
            {canManage ? (
              <form className="space-y-4" onSubmit={save} noValidate aria-labelledby="cu-details">
                <CustomerFields f={f} set={(k, v) => setF((p) => ({ ...p, [k]: v }))} problems={problems} groups={groups} isNew={false} />
                <div className="flex justify-end gap-2">
                  <button type="button" className="btn-ghost" disabled={!patch || busy} onClick={() => setF(formFrom(c))}>
                    Undo changes
                  </button>
                  <button className="btn-primary" disabled={!patch || busy}>
                    Save changes
                  </button>
                </div>
              </form>
            ) : (
              <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-[10rem_1fr]">
                <dt className="text-ink-muted">Reference</dt>
                <dd>{c.external_ref ?? "—"}</dd>
                <dt className="text-ink-muted">Email</dt>
                <dd>{c.email ?? "No email"}</dd>
                <dt className="text-ink-muted">Phone</dt>
                <dd>{c.phone ?? "—"}</dd>
                <dt className="text-ink-muted">Group</dt>
                <dd>{group?.name ?? "No group"}</dd>
                <dt className="text-ink-muted">Payment terms</dt>
                <dd>{termsLabel(c.payment_terms_days)}</dd>
              </dl>
            )}
          </section>
          <div className="space-y-5">
            <Balance customerId={c.id} />
          </div>
        </div>
      )}
    </div>
  );
}

function Balance({ customerId }: { customerId: string }) {
  const { session } = useAuth();
  const allowed = !!session && has(session, "invoice.read") && (has(session, "payment.record") || has(session, "payment.verify"));
  const [row, setRow] = useState<AgingCustomer | null | undefined>(undefined);
  useEffect(() => {
    if (allowed)
      api.receivables().then(
        (r) => setRow(r.customers.find((x) => x.customer_id === customerId) ?? null),
        () => setRow(undefined),
      );
  }, [allowed, customerId]);
  if (!allowed || row === undefined) return null;
  const late = row ? dec(row.d1_30) + dec(row.d31_60) + dec(row.d61_90) + dec(row.d90_plus) : 0n;
  return (
    <section className="card space-y-3 p-6" aria-labelledby="cu-balance">
      <CardTitle id="cu-balance" title="Balance" />
      {row ? (
        <dl className="grid grid-cols-2 gap-y-2 text-sm">
          <dt className="text-ink-muted">Owed</dt>
          <dd className="num text-right text-base font-semibold">{money(row.total)}</dd>
          <dt className="text-ink-muted">Late</dt>
          <dd className={`num text-right ${late > 0n ? "font-semibold text-danger" : ""}`}>{cad(late)}</dd>
          <dt className="text-ink-muted">Confirmed, not applied</dt>
          <dd className="num text-right">{money(row.unapplied_credit)}</dd>
          <dt className="text-ink-muted">Reported, not confirmed</dt>
          <dd className="num text-right">{money(row.reported_pending)}</dd>
        </dl>
      ) : (
        <p className="text-sm text-ink-muted">Nothing is owed.</p>
      )}
      <Link className={`${link} text-sm`} to="/receivables">
        All receivables
      </Link>
    </section>
  );
}

/** Customer › Invoices tab: every invoice, newest first, 50 at a time. */
function CustomerInvoices({ customerId }: { customerId: string }) {
  const [rows, setRows] = useState<InvoiceSummary[] | null>(null);
  const [next, setNext] = useState<string | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState(false);
  const page = useCallback(
    async (cursor?: string) => {
      try {
        const p = await api.invoices({
          customer_id: customerId,
          limit: 50,
          cursor,
        });
        setRows((r) => (cursor && r ? [...r, ...p.items] : p.items));
        setNext(p.next_cursor);
        setTotal(p.total);
        setError(false);
      } catch {
        setError(true);
      }
    },
    [customerId],
  );
  useEffect(() => void page(), [page]);
  const open = rows?.filter((i) => i.state === "ISSUED" || i.state === "PARTIALLY_PAID") ?? [];
  const cols: Column<InvoiceSummary>[] = [
    {
      key: "n",
      header: "Invoice",
      cell: (i) => (
        <Link className={`${link} whitespace-nowrap`} to={`/invoices/${i.id}`}>
          {i.invoice_no}
        </Link>
      ),
      sort: (i) => i.invoice_no,
    },
    {
      key: "o",
      header: "Order",
      cell: (i) => <span className="whitespace-nowrap">{i.order_no}</span>,
    },
    {
      key: "d",
      header: "Issued",
      cell: (i) => <span className="whitespace-nowrap">{day(i.issued_at)}</span>,
      sort: (i) => i.issued_at,
    },
    {
      key: "u",
      header: "Due",
      cell: (i) => <span className="whitespace-nowrap">{day(i.due_at)}</span>,
      sort: (i) => i.due_at,
    },
    {
      key: "t",
      header: "Total",
      align: "right",
      cell: (i) => <span className="num whitespace-nowrap">{money(i.total)}</span>,
      sort: (i) => dec(i.total),
    },
    {
      key: "b",
      header: "Balance",
      align: "right",
      cell: (i) => <span className="num whitespace-nowrap font-semibold">{money(i.balance)}</span>,
      sort: (i) => dec(i.balance),
    },
    {
      key: "s",
      header: "State",
      cell: (i) => <StatusChip tone={INVOICE_STATE[i.state].tone}>{INVOICE_STATE[i.state].label}</StatusChip>,
    },
  ];
  return (
    <section className="card space-y-4 p-6" aria-labelledby="cu-invoices">
      <CardTitle id="cu-invoices" title="Invoices" />
      {error ? (
        <ErrorState title="Couldn't load invoices" onRetry={() => void page()} />
      ) : rows === null ? (
        <LoadingRows rows={4} cols={6} />
      ) : (
        <>
          <p className="text-sm text-ink-muted">
            {total} {total === 1 ? "invoice" : "invoices"}
            {open.length > 0 && ` · ${open.length} open`}
          </p>
          <DataGrid rows={rows} columns={cols} rowKey={(i) => i.id} caption="Customer invoices" empty={<EmptyState title="No invoices yet" />} />
          {next && (
            <button type="button" className="btn-ghost" onClick={() => void page(next)}>
              Show more
            </button>
          )}
        </>
      )}
    </section>
  );
}

/** Customer › Orders tab: every sales order, newest first, 50 at a time. */
function CustomerOrders({ customerId }: { customerId: string }) {
  const [rows, setRows] = useState<OrderSummary[] | null>(null);
  const [next, setNext] = useState<string | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState(false);
  const page = useCallback(
    async (cursor?: string) => {
      try {
        const p = await api.orders({
          customer_id: customerId,
          limit: 50,
          cursor,
        });
        setRows((r) => (cursor && r ? [...r, ...p.items] : p.items));
        setNext(p.next_cursor);
        setTotal(p.total);
        setError(false);
      } catch {
        setError(true);
      }
    },
    [customerId],
  );
  useEffect(() => void page(), [page]);
  const cols: Column<OrderSummary>[] = [
    {
      key: "n",
      header: "Order",
      cell: (o) => (
        <Link className={`${link} whitespace-nowrap`} to={`/orders/${o.id}`}>
          {o.order_no}
        </Link>
      ),
      sort: (o) => o.order_no,
    },
    {
      key: "d",
      header: "Created",
      cell: (o) => <span className="whitespace-nowrap">{day(o.created_at)}</span>,
      sort: (o) => o.created_at,
    },
    {
      key: "r",
      header: "Wanted for",
      cell: (o) => (o.requested_for ? <span className="whitespace-nowrap">{day(o.requested_for)}</span> : "—"),
    },
    {
      key: "l",
      header: "Lines",
      align: "right",
      cell: (o) => <span className="num">{o.line_count}</span>,
    },
    {
      key: "s",
      header: "Status",
      cell: (o) => <StatusChip tone={ORDER_STATUS[o.status].tone}>{ORDER_STATUS[o.status].label}</StatusChip>,
    },
  ];
  return (
    <section className="card space-y-4 p-6" aria-labelledby="cu-orders">
      <CardTitle id="cu-orders" title="Orders" />
      {error ? (
        <ErrorState title="Couldn't load orders" onRetry={() => void page()} />
      ) : rows === null ? (
        <LoadingRows rows={4} cols={5} />
      ) : (
        <>
          <p className="text-sm text-ink-muted">
            {total} {total === 1 ? "order" : "orders"}
          </p>
          <DataGrid rows={rows} columns={cols} rowKey={(o) => o.id} caption="Customer orders" empty={<EmptyState title="No orders yet" />} />
          {next && (
            <button type="button" className="btn-ghost" onClick={() => void page(next)}>
              Show more
            </button>
          )}
        </>
      )}
    </section>
  );
}
