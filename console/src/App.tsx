import { createContext, useContext, useEffect, useState } from "react";
import { Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { getSession, setSession, type Session } from "./lib/api";
import { useLocalState } from "./lib/hooks";
import { Layout } from "./components/Layout";
import Login from "./pages/Login";
import Home from "./pages/Home";
import Knowledge from "./pages/Knowledge";
import DocumentPage from "./pages/Document";
import Chat from "./pages/Chat";
import Search from "./pages/Search";
import Research from "./pages/Research";
import Memory from "./pages/Memory";
import Traces from "./pages/Traces";
import TraceDetail from "./pages/TraceDetail";
import Sessions from "./pages/Sessions";
import Users from "./pages/Users";
import Scores from "./pages/Scores";
import Usage from "./pages/Usage";
import Settings from "./pages/Settings";

type Ctx = { session: Session; rangeHours: number; setRangeHours: (h: number) => void; logout: () => void };
const AppCtx = createContext<Ctx | null>(null);
export const useApp = () => useContext(AppCtx)!;

export default function App() {
  const [session, setS] = useState<Session | null>(getSession());
  const [rangeHours, setRangeHours] = useLocalState("atlas.range", 24);
  const nav = useNavigate();

  useEffect(() => {
    const onUnauth = () => { setSession(null); setS(null); };
    window.addEventListener("atlas:unauthorized", onUnauth);
    return () => window.removeEventListener("atlas:unauthorized", onUnauth);
  }, []);

  if (!session) {
    return <Login onLogin={(s) => { setSession(s); setS(s); nav("/"); }} />;
  }
  const logout = () => { setSession(null); setS(null); };

  return (
    <AppCtx.Provider value={{ session, rangeHours, setRangeHours, logout }}>
      <Layout>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/knowledge" element={<Knowledge />} />
          <Route path="/knowledge/:cid" element={<Knowledge />} />
          <Route path="/documents/:did" element={<DocumentPage />} />
          <Route path="/chat" element={<Chat />} />
          <Route path="/chat/:convId" element={<Chat />} />
          <Route path="/search" element={<Search />} />
          <Route path="/research" element={<Research />} />
          <Route path="/research/:jobId" element={<Research />} />
          <Route path="/memory" element={<Memory />} />
          <Route path="/traces" element={<Traces />} />
          <Route path="/traces/:traceId" element={<TraceDetail />} />
          <Route path="/sessions" element={<Sessions />} />
          <Route path="/sessions/:sessionId" element={<Sessions />} />
          <Route path="/users" element={<Users />} />
          <Route path="/scores" element={<Scores />} />
          <Route path="/usage" element={<Usage />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </Layout>
    </AppCtx.Provider>
  );
}
