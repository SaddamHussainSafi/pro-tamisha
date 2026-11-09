import { Link, Navigate, useLocation } from "react-router-dom";
import { hasCompanyWide } from "../api";
import { useAuth } from "../auth";
import { Icon, type IconName } from "../ui/Icon";
import { PageHeader } from "../ui/primitives";
import { MyAccount } from "./settings/MyAccount";
import { Roles } from "./settings/Roles";
import { Security } from "./settings/Security";
import { Users } from "./settings/Users";

type Tab = { to: string; key: string; label: string; icon: IconName; admin: boolean };
const TABS: Tab[] = [
  { to: "/settings", key: "account", label: "My account", icon: "user", admin: false },
  { to: "/settings/users", key: "users", label: "User management", icon: "users", admin: true },
  { to: "/settings/roles", key: "roles", label: "Roles & permissions", icon: "layers", admin: true },
  { to: "/settings/security", key: "security", label: "Security & sessions", icon: "shield", admin: true },
];

/** Settings: My account for everyone; user management, roles and security for Super Admin. */
export function SettingsPage() {
  const { session } = useAuth();
  const loc = useLocation();
  if (!session) return null;
  const isAdmin = hasCompanyWide(session, "employee.manage");
  const tabs = TABS.filter((t) => isAdmin || !t.admin);
  const current: Tab = TABS.find((t) => t.to === loc.pathname) ?? (TABS[0] as Tab);
  if (current.admin && !isAdmin) return <Navigate to="/settings" replace />;
  return (
    <div className="space-y-5">
      <PageHeader title="Settings" subtitle={isAdmin ? "Your account, the people who can sign in, and how sign-in is protected." : "Your account and how you sign in."} />
      {tabs.length > 1 && (
        <nav aria-label="Settings sections" className="flex flex-wrap gap-1.5">
          {tabs.map((t) => (
            <Link
              key={t.key}
              to={t.to}
              aria-current={current.key === t.key ? "page" : undefined}
              className={`chip px-4 py-2 text-sm ${current.key === t.key ? "border-brand-secondary bg-brand-secondary text-white" : "border-line bg-card text-ink hover:bg-subtle"}`}
            >
              <Icon name={t.icon} size={16} />
              {t.label}
            </Link>
          ))}
        </nav>
      )}
      {current.key === "users" ? <Users /> : current.key === "roles" ? <Roles /> : current.key === "security" ? <Security /> : <MyAccount />}
    </div>
  );
}
