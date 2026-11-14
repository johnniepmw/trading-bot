// Composition root and HTTP adapter. Example app: replace the routes during bootstrap,
// keep /healthz (smoke tests and uptime checks depend on it).
import { createServer, type ServerResponse } from "node:http";
import { greeting } from "./core/greeting.ts";
import { Flags, gcsSource, loadRegistry } from "./flags.ts";

const version = process.env["APP_VERSION"] ?? "dev";
const port = Number(process.env["PORT"] ?? "8080");
const bucket = process.env["FLAGS_BUCKET"];
const flags = new Flags(loadRegistry(), bucket ? { source: gcsSource(bucket) } : {});

function log(severity: "INFO" | "WARNING" | "ERROR", message: string, extra: object = {}): void {
  console.log(JSON.stringify({ severity, message, version, ...extra }));
}

function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

function send(res: ServerResponse, status: number, type: string, body: string): void {
  res.writeHead(status, { "content-type": type, "cache-control": "no-store" });
  res.end(body);
}

const server = createServer((req, res) => {
  const url = new URL(req.url ?? "/", "http://localhost");
  try {
    if (url.pathname === "/healthz") {
      send(res, 200, "application/json", JSON.stringify({ status: "ok", version }));
    } else if (url.pathname === "/" && req.method === "GET") {
      const heading = flags.isEnabled("home-greeting") ? greeting(url.searchParams.get("name")) : "Welcome";
      send(
        res,
        200,
        "text/html; charset=utf-8",
        `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>App</title></head>` +
          `<body><h1>${escapeHtml(heading)}</h1><p>version ${escapeHtml(version)}</p></body></html>`,
      );
    } else {
      send(res, 404, "application/json", JSON.stringify({ error: "not found" }));
    }
  } catch (error) {
    log("ERROR", "request failed", { path: url.pathname, error: String(error) });
    send(res, 500, "application/json", JSON.stringify({ error: "internal error" }));
  }
});

server.listen(port, () => {
  log("INFO", "listening", { port });
});
process.on("SIGTERM", () => {
  server.close(() => {
    process.exit(0);
  });
});
