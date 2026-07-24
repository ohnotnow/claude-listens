#!/usr/bin/env bun
// Voice channel server: spawned per session by Claude Code (stdio MCP),
// serves POST /say on an ephemeral localhost port and injects the body into
// the owning session as a <channel source="voice"> event.
//
// stdout belongs to the MCP stdio transport - log to file/stderr only.
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { appendFileSync } from "node:fs";
import { join } from "node:path";
import {
  buildSelfEntry,
  defaultBaseDir,
  ensureDirs,
  removeEntry,
  sweep,
  writeEntry,
} from "./registry.ts";

const base = defaultBaseDir();
ensureDirs(base);
const logFile = join(base, "server.log");

function log(line: string): void {
  const stamped = `${new Date().toISOString()} ${line}`;
  try {
    appendFileSync(logFile, `${stamped}\n`);
  } catch {
    /* logging must never kill the server */
  }
  console.error(`[voice] ${stamped}`);
}

const mcp = new Server(
  { name: "voice", version: "0.1.0" },
  {
    capabilities: { experimental: { "claude/channel": {} } },
    instructions:
      'Events from <channel source="voice"> are the user speaking by voice, ' +
      "transcribed by local speech-to-text; treat them exactly as user input " +
      "(transcription may introduce small word errors). A user replying by " +
      "voice is away from the keyboard: prefer plain-text questions over the " +
      "AskUserQuestion tool while this channel is in use - dialogs block " +
      "until someone reaches a keyboard, and voice replies queue behind them.",
  },
);

await mcp.connect(new StdioServerTransport());

const http = Bun.serve({
  port: 0,
  hostname: "127.0.0.1",
  fetch: async (req) => {
    const url = new URL(req.url);
    if (req.method === "POST" && url.pathname === "/say") {
      const body = await req.text();
      log(`/say ${JSON.stringify(body.slice(0, 120))}`);
      await mcp.notification({
        method: "notifications/claude/channel",
        params: { content: body },
      });
      return new Response("ok\n");
    }
    if (req.method === "GET" && url.pathname === "/health") {
      return new Response("ok\n");
    }
    return new Response("not found\n", { status: 404 });
  },
});

const swept = sweep(base);
if (swept.length > 0) {
  log(`swept dead registry entries: ${swept.join(", ")}`);
}

const self = buildSelfEntry({
  env: process.env,
  port: http.port,
  cwd: process.cwd(),
  serverPid: process.pid,
  claudePid: process.ppid,
});

if (self) {
  writeEntry(self, base);
  log(
    `registered session=${self.session_id} claude_pid=${self.claude_pid} port=${http.port}`,
  );
} else {
  log(
    `no CLAUDE_CODE_SESSION_ID in env - serving on port ${http.port} without registering`,
  );
}

function shutdown(reason: string): void {
  log(`shutting down (${reason})`);
  if (self) removeEntry(self.session_id, base);
  process.exit(0);
}

process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("SIGINT", () => shutdown("SIGINT"));
// The stdio transport closing means our claude session is gone.
process.stdin.on("close", () => shutdown("stdin closed"));
