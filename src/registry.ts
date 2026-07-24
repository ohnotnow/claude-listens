// Session registry: one JSON file per live Claude Code session, keyed by
// session id, under ~/.claude-voice/registry/. Shared contract lives in the
// ait Phase 1 epic (handy-UkLWZ.2).
import {
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

export interface RegistryEntry {
  session_id: string;
  claude_pid: number;
  server_pid: number;
  port: number;
  cwd: string;
  started_at: string;
  name: string | null;
}

export function defaultBaseDir(): string {
  return join(homedir(), ".claude-voice");
}

export function registryDir(base: string = defaultBaseDir()): string {
  return join(base, "registry");
}

export function ensureDirs(base: string = defaultBaseDir()): void {
  mkdirSync(registryDir(base), { recursive: true, mode: 0o700 });
}

export function entryPath(
  sessionId: string,
  base: string = defaultBaseDir(),
): string {
  return join(registryDir(base), `${sessionId}.json`);
}

export function writeEntry(
  entry: RegistryEntry,
  base: string = defaultBaseDir(),
): void {
  ensureDirs(base);
  writeFileSync(
    entryPath(entry.session_id, base),
    `${JSON.stringify(entry, null, 2)}\n`,
  );
}

export function readEntry(
  sessionId: string,
  base: string = defaultBaseDir(),
): RegistryEntry | null {
  try {
    return JSON.parse(
      readFileSync(entryPath(sessionId, base), "utf8"),
    ) as RegistryEntry;
  } catch {
    return null;
  }
}

export function removeEntry(
  sessionId: string,
  base: string = defaultBaseDir(),
): void {
  rmSync(entryPath(sessionId, base), { force: true });
}

/** Identity handling: decide this server's registry entry from its spawn
 *  environment. Returns null when there is no owning session to register
 *  against (e.g. run standalone for a smoke test). */
export function buildSelfEntry(input: {
  env: Record<string, string | undefined>;
  port: number;
  cwd: string;
  serverPid: number;
  claudePid: number;
}): RegistryEntry | null {
  const sessionId = input.env.CLAUDE_CODE_SESSION_ID;
  if (!sessionId) return null;
  return {
    session_id: sessionId,
    claude_pid: input.claudePid,
    server_pid: input.serverPid,
    port: input.port,
    cwd: input.cwd,
    started_at: new Date().toISOString(),
    name: null, // reserved for Phase 2 named sessions
  };
}

export function pidAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

/** Remove entries whose owning claude process is gone (or that are unreadable).
 *  Returns the removed filenames. `isAlive` is injectable for tests. */
export function sweep(
  base: string = defaultBaseDir(),
  isAlive: (pid: number) => boolean = pidAlive,
): string[] {
  const dir = registryDir(base);
  if (!existsSync(dir)) return [];
  const removed: string[] = [];
  for (const file of readdirSync(dir)) {
    if (!file.endsWith(".json")) continue;
    const path = join(dir, file);
    let dead = true;
    try {
      const entry = JSON.parse(readFileSync(path, "utf8")) as RegistryEntry;
      dead = !Number.isInteger(entry.claude_pid) || !isAlive(entry.claude_pid);
    } catch {
      // unreadable entry: treat as dead
    }
    if (dead) {
      rmSync(path, { force: true });
      removed.push(file);
    }
  }
  return removed;
}
