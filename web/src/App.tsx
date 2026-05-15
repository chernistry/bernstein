import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import AppShell from './components/AppShell';
import { ThemeProvider } from './components/ThemeProvider';
import Tasks from './routes/Tasks';
import Agents from './routes/Agents';
import Approvals from './routes/Approvals';
import Audit from './routes/Audit';
import Costs from './routes/Costs';
import Fleet from './routes/Fleet';
import Settings from './routes/Settings';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 5_000, retry: 1, refetchOnWindowFocus: false },
  },
});

export default function App() {
  return (
    <ThemeProvider defaultTheme="system" storageKey="bernstein-theme">
      <QueryClientProvider client={queryClient}>
        <BrowserRouter basename="/ui">
          <AppShell>
            <Routes>
              <Route path="/" element={<Navigate to="/tasks" replace />} />
              <Route path="/tasks" element={<Tasks />} />
              <Route path="/agents" element={<Agents />} />
              <Route path="/approvals" element={<Approvals />} />
              <Route path="/audit" element={<Audit />} />
              <Route path="/costs" element={<Costs />} />
              {/* Fleet + Settings live in topbar / user-menu but stay deep-linkable. */}
              <Route path="/fleet" element={<Fleet />} />
              <Route path="/settings" element={<Settings />} />
            </Routes>
          </AppShell>
        </BrowserRouter>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
