import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import App from "./App";

const okLogin = {
  access_token: "token-admin",
  token_type: "bearer",
  expires_in: 3600
};

function mockFetch(handler: (input: RequestInfo | URL, init?: RequestInit) => Response | Promise<Response>) {
  vi.stubGlobal("fetch", vi.fn(handler));
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" }
  });
}

describe("App authentication", () => {
  beforeEach(() => {
    localStorage.clear();
    window.history.pushState({}, "", "/");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  test("disables login while username or password is empty", async () => {
    const user = userEvent.setup();

    render(<App />);

    const button = screen.getByRole("button", { name: "登录" });
    expect(button).toBeDisabled();

    await user.type(screen.getByLabelText("账号"), "admin");
    expect(button).toBeDisabled();
  });

  test("keeps username and clears password after failed login", async () => {
    mockFetch(() =>
      jsonResponse(
        {
          error: {
            code: "unauthorized",
            message: "Invalid username or password",
            details: null,
            trace_id: "trace"
          }
        },
        401
      )
    );
    const user = userEvent.setup();

    render(<App />);

    await user.type(screen.getByLabelText("账号"), "admin");
    await user.type(screen.getByLabelText("密码"), "wrong");
    await user.click(screen.getByRole("button", { name: "登录" }));

    expect(await screen.findByText("登录已失效，请重新登录。")).toBeInTheDocument();
    expect(screen.getByLabelText("账号")).toHaveValue("admin");
    expect(screen.getByLabelText("密码")).toHaveValue("");
  });

  test("redirects unauthenticated users to login", () => {
    window.history.pushState({}, "", "/qa");

    render(<App />);

    expect(screen.getByRole("heading", { name: "登录" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/login");
  });

  test("logs in and renders the protected workspace", async () => {
    mockFetch((input) => {
      const url = String(input);
      if (url.endsWith("/auth/login")) {
        return jsonResponse(okLogin);
      }
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ username: "admin", role: "admin" });
      }
      return jsonResponse({}, 404);
    });
    const user = userEvent.setup();

    render(<App />);

    await user.type(screen.getByLabelText("账号"), "admin");
    await user.type(screen.getByLabelText("密码"), "secret");
    await user.click(screen.getByRole("button", { name: "登录" }));

    expect(await screen.findByRole("heading", { name: "问答" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "资料库" })).toBeInTheDocument();
  });

  test("hides maintenance navigation for users without permission", async () => {
    localStorage.setItem(
      "agromech.session",
      JSON.stringify({ token: "token-user", username: "readonly", role: "user" })
    );
    mockFetch((input) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ username: "readonly", role: "user" });
      }
      return jsonResponse({}, 404);
    });

    render(<App />);

    await screen.findByRole("heading", { name: "问答" });
    expect(screen.queryByRole("link", { name: "资料库" })).not.toBeInTheDocument();
  });

  test("clears expired sessions and returns to login", async () => {
    localStorage.setItem(
      "agromech.session",
      JSON.stringify({ token: "expired", username: "admin", role: "admin" })
    );
    mockFetch(() =>
      jsonResponse(
        {
          error: {
            code: "unauthorized",
            message: "Invalid or expired access token",
            details: null,
            trace_id: "trace"
          }
        },
        401
      )
    );

    render(<App />);

    await waitFor(() => expect(window.location.pathname).toBe("/login"));
    expect(localStorage.getItem("agromech.session")).toBeNull();
    expect(screen.getByRole("heading", { name: "登录" })).toBeInTheDocument();
  });
});
