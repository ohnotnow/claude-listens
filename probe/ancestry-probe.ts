#!/usr/bin/env bun
// Phase 0 probe (ait handy-UkLWZ.1.1): log our own process ancestry, cwd and
// CLAUDE/MCP env names so the PID matcher can be decided from real evidence,
// then take POSTs on 18788 and forward them as channel events.
//
// stdout belongs to the MCP stdio transport — never write to it directly.
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { execFileSync } from "node:child_process";
import { appendFileSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const dir = join(homedir(), ".claude-voice");
const logFile = join(dir, "probe.log");

function log(line: string): void {
  try {
    appendFileSync(logFile, `${line}\n`);
  } catch {
    /* logging must never kill the probe */
  }
  console.error(`[probe] ${line}`);
}

mkdirSync(dir, { recursive: true, mode: 0o700 });
log(`=== probe start ${new Date().toISOString()} pid=${process.pid} ===`);

try {
  let pid = process.pid;
  for (let i = 0; i < 25 && pid > 1; i++) {
    const out = execFileSync(
      "ps",
      ["-o", "pid=,ppid=,comm=,command=", "-p", String(pid)],
      { encoding: "utf8" },
    ).trim();
    log(`ancestor: ${out}`);
    const ppid = Number.parseInt(out.split(/\s+/)[1] ?? "", 10);
    if (!Number.isFinite(ppid) || ppid === pid) break;
    pid = ppid;
  }
} catch (e) {
  log(`ancestry walk failed: ${e}`);
}

log(`cwd: ${process.cwd()}`);
for (const name of Object.keys(process.env).sort()) {
  if (!/CLAUDE|MCP/i.test(name)) continue;
  // Names always; values only when the name can't be credential-shaped.
  const value = /TOKEN|KEY|SECRET|AUTH|PASS/i.test(name)
    ? "<redacted>"
    : process.env[name];
  log(`env: ${name}=${value}`);
}

const mcp = new Server(
  { name: "voice", version: "0.0.1" },
  {
    capabilities: { experimental: { "claude/channel": {} } },
    instructions:
      'Events from <channel source="voice"> are probe test messages from a local experiment; acknowledge them briefly.',
  },
);

await mcp.connect(new StdioServerTransport());
log("mcp connected over stdio");

try {
  Bun.serve({
    port: 18788,
    hostname: "127.0.0.1",
    fetch: async (req) => {
      const body = await req.text();
      log(`POST received: ${body}`);
      await mcp.notification({
        method: "notifications/claude/channel",
        params: { content: body },
      });
      log("notification written to transport");
      return new Response("ok\n");
    },
  });
  log("http listening on 127.0.0.1:18788");
} catch (e) {
  log(`http bind failed: ${e}`);
}
