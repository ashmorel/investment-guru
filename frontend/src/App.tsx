import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { BrowserRouter, NavLink, Navigate, Outlet, Route, Routes } from "react-router-dom";
import { apiFetch } from "./lib/api";
import type { Me } from "./lib/types";
import AdminPage from "./pages/AdminPage";
import DashboardPage from "./pages/DashboardPage";
import GuruPage from "./pages/GuruPage";
import ImportWizardPage from "./pages/ImportWizardPage";
import LoginPage from "./pages/LoginPage";
import OrsoPage from "./pages/OrsoPage";
import IngestWizard from "./pages/orso/IngestWizard";
import PortfolioDetailPage from "./pages/PortfolioDetailPage";
import PortfoliosPage from "./pages/PortfoliosPage";
import SectorsPage from "./pages/SectorsPage";
import SettingsPage from "./pages/SettingsPage";

const queryClient = new QueryClient();

function NavItem({ to, label }: { to: string; label: string }) {
  return (
    <li>
      <NavLink
        to={to}
        className={({ isActive }) =>
          `block rounded-md px-3 py-2 ${
            isActive ? "bg-accent-subtle text-accent font-medium" : "text-text hover:bg-accent-subtle"
          }`
        }
      >
        {label}
      </NavLink>
    </li>
  );
}

function RequireAuth() {
  const me = useQuery({
    queryKey: ["me"],
    queryFn: () => apiFetch<Me>("/api/auth/me"),
    retry: false,
  });
  if (me.isPending) return <div className="p-8 text-muted">Loading…</div>;
  if (me.isError) return <Navigate to="/login" replace />;
  return (
    <div data-testid="app-shell" className="flex min-h-screen flex-col bg-bg md:flex-row">
      <nav className="w-full shrink-0 border-b border-border bg-surface p-4 md:min-h-screen md:w-56 md:border-r md:border-b-0">
        <p className="mb-3 font-semibold text-text md:mb-6">Investment Guru</p>
        <ul className="flex flex-wrap gap-1 text-sm md:block md:space-y-1">
          <NavItem to="/" label="Dashboard" />
          <NavItem to="/portfolios" label="Portfolios" />
          <NavItem to="/sectors" label="Sectors" />
          <NavItem to="/guru" label="Guru" />
          <NavItem to="/orso" label="ORSO" />
          <NavItem to="/import" label="Import CSV" />
          <NavItem to="/settings" label="Settings" />
          {me.data?.is_admin && <NavItem to="/admin" label="Admin" />}
        </ul>
      </nav>
      <main className="min-w-0 flex-1 p-4 md:p-8">
        <Outlet />
      </main>
    </div>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route element={<RequireAuth />}>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/portfolios" element={<PortfoliosPage />} />
            <Route path="/portfolios/:id" element={<PortfolioDetailPage />} />
            <Route path="/sectors" element={<SectorsPage />} />
            <Route path="/guru" element={<GuruPage />} />
            <Route path="/orso" element={<OrsoPage />} />
            <Route path="/orso/import" element={<IngestWizard />} />
            <Route path="/import" element={<ImportWizardPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/admin" element={<AdminPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
