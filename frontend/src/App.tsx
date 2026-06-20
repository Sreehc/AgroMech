import { FormEvent, useEffect, useMemo, useState } from "react";

import { ApiRequestError, currentUser, login } from "./api/auth";
import { errorMessage } from "./api/errors";
import {
  canMaintainLibrary,
  clearSession,
  loadSession,
  saveSession,
  type Session
} from "./auth/session";
import "./styles.css";

function guardedInitialPath(session: Session | null): string {
  const path = window.location.pathname;
  if (!session && path !== "/login") {
    window.history.replaceState({}, "", "/login");
    return "/login";
  }
  if (session && (path === "/" || path === "/login")) {
    window.history.replaceState({}, "", "/qa");
    return "/qa";
  }
  return path;
}

function navigate(path: string, setPath: (path: string) => void): void {
  window.history.pushState({}, "", path);
  setPath(path);
}

function LoginPage({
  onAuthenticated
}: {
  onAuthenticated: (session: Session) => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const disabled = username.trim() === "" || password === "" || submitting;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (disabled) {
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      const token = await login(username.trim(), password);
      const user = await currentUser(token.access_token);
      onAuthenticated({
        token: token.access_token,
        username: user.username,
        role: user.role
      });
    } catch (caught) {
      if (caught instanceof ApiRequestError) {
        setError(errorMessage(caught.response));
      } else {
        setError("服务暂时不可用，请稍后重试。");
      }
      setPassword("");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-shell">
      <section className="login-panel" aria-labelledby="login-title">
        <p className="eyebrow">AgroMech RAG</p>
        <h1 id="login-title">登录</h1>
        <form className="login-form" onSubmit={submit}>
          <label>
            <span>账号</span>
            <input
              autoComplete="username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
            />
          </label>
          <label>
            <span>密码</span>
            <input
              autoComplete="current-password"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </label>
          {error ? (
            <p className="form-error" role="alert">
              {error}
            </p>
          ) : null}
          <button type="submit" disabled={disabled}>
            {submitting ? "登录中" : "登录"}
          </button>
        </form>
      </section>
    </main>
  );
}

function Workspace({
  session,
  path,
  onNavigate,
  onLogout
}: {
  session: Session;
  path: string;
  onNavigate: (path: string) => void;
  onLogout: () => void;
}) {
  const title = useMemo(() => {
    if (path === "/image-question") {
      return "图片提问";
    }
    if (path === "/library") {
      return "资料库";
    }
    return "问答";
  }, [path]);

  return (
    <div className="workspace-shell">
      <aside className="sidebar" aria-label="主导航">
        <p className="eyebrow">AgroMech RAG</p>
        <nav>
          <a href="/qa" onClick={(event) => {
            event.preventDefault();
            onNavigate("/qa");
          }}>
            问答
          </a>
          <a href="/image-question" onClick={(event) => {
            event.preventDefault();
            onNavigate("/image-question");
          }}>
            图片提问
          </a>
          {canMaintainLibrary(session) ? (
            <a href="/library" onClick={(event) => {
              event.preventDefault();
              onNavigate("/library");
            }}>
              资料库
            </a>
          ) : null}
        </nav>
        <div className="user-block">
          <span>{session.username}</span>
          <button type="button" onClick={onLogout}>
            退出
          </button>
        </div>
      </aside>
      <main className="workspace-main">
        <header>
          <h1>{title}</h1>
        </header>
        <section className="placeholder-panel">
          <p>当前页面已受登录态保护。</p>
        </section>
      </main>
    </div>
  );
}

function App() {
  const [session, setSession] = useState<Session | null>(() => loadSession());
  const [path, setPath] = useState(() => guardedInitialPath(loadSession()));

  useEffect(() => {
    function handlePopState() {
      setPath(guardedInitialPath(loadSession()));
    }
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  useEffect(() => {
    if (!session) {
      if (path !== "/login") {
        window.history.replaceState({}, "", "/login");
        setPath("/login");
      }
    }
  }, [path, session]);

  useEffect(() => {
    if (!session) {
      return;
    }
    let cancelled = false;
    currentUser(session.token)
      .then((user) => {
        if (cancelled) {
          return;
        }
        const refreshed = { token: session.token, username: user.username, role: user.role };
        if (user.username !== session.username || user.role !== session.role) {
          setSession(refreshed);
          saveSession(refreshed);
        }
      })
      .catch((caught) => {
        if (cancelled) {
          return;
        }
        if (caught instanceof ApiRequestError && caught.response.error.code === "unauthorized") {
          clearSession();
          setSession(null);
          window.history.replaceState({}, "", "/login");
          setPath("/login");
        }
      });

    return () => {
      cancelled = true;
    };
  }, [session?.token]);

  function handleAuthenticated(nextSession: Session) {
    saveSession(nextSession);
    setSession(nextSession);
    navigate("/qa", setPath);
  }

  function handleLogout() {
    clearSession();
    setSession(null);
    navigate("/login", setPath);
  }

  if (!session) {
    return <LoginPage onAuthenticated={handleAuthenticated} />;
  }

  return (
    <Workspace
      session={session}
      path={path}
      onNavigate={(nextPath) => navigate(nextPath, setPath)}
      onLogout={handleLogout}
    />
  );
}

export default App;
