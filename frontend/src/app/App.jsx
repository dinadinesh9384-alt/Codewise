import { lazy, Suspense } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import AppLayout from "../layouts/AppLayout.jsx";
import PlannedPage from "../pages/PlannedPage.jsx";
import NotFoundPage from "../pages/NotFoundPage.jsx";
import RepositorySubmissionPage from "../pages/RepositorySubmissionPage.jsx";
import LoginPage from "../pages/LoginPage.jsx";
import RequireSession from "../components/RequireSession.jsx";
import { SessionProvider } from "../state/SessionProvider.jsx";
import { ThemeProvider } from "../state/ThemeProvider.jsx";
import { TooltipProvider } from "@/components/ui/tooltip";

const HealthPage = lazy(() => import("../pages/HealthPage.jsx"));
const ExecutionPage = lazy(() => import("../pages/ExecutionPage.jsx"));
const HistoryPage = lazy(() => import("../pages/HistoryPage.jsx"));
const AgentWorkspacePage = lazy(() => import("../pages/AgentWorkspacePage.jsx"));

function PageLoading() {
  return (
    <div className="grid min-h-64 place-content-center text-sm text-muted-foreground" role="status" aria-live="polite">
      <span>Loading workspace…</span>
    </div>
  );
}

function LazyPage({ children }) {
  return <Suspense fallback={<PageLoading />}>{children}</Suspense>;
}

export default function App() {
  return (
    <ThemeProvider>
      <TooltipProvider>
        <SessionProvider>
        <BrowserRouter>
          <Routes>
            <Route path="login" element={<LoginPage />} />
            <Route element={<RequireSession><AppLayout /></RequireSession>}>
              <Route index element={<LazyPage><HealthPage /></LazyPage>} />
              <Route path="repositories" element={<RepositorySubmissionPage />} />
              <Route path="history" element={<LazyPage><HistoryPage /></LazyPage>} />
              <Route path="agents" element={<LazyPage><AgentWorkspacePage /></LazyPage>} />
              <Route path="executions/:executionId/agents/:agentId" element={<LazyPage><ExecutionPage /></LazyPage>} />
              <Route path="executions/:executionId" element={<LazyPage><ExecutionPage /></LazyPage>} />
              <Route path="architecture" element={<PlannedPage title="Architecture intelligence" description="Architecture maps and dependency insights are planned for a future release." />} />
              <Route path="settings" element={<PlannedPage title="Settings" description="Workspace and account settings will be available in a future release." />} />
              <Route path="*" element={<NotFoundPage />} />
            </Route>
          </Routes>
        </BrowserRouter>
        </SessionProvider>
      </TooltipProvider>
    </ThemeProvider>
  );
}
