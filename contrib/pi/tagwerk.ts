import { spawn } from 'node:child_process';
import { homedir } from 'node:os';
import { join } from 'node:path';
import type { ExtensionAPI, ExtensionContext } from '@earendil-works/pi-coding-agent';

// Symlink as ~/.pi/agent/extensions/tagwerk.ts; pi runs under Bun with its own PATH, so spawn by path.
const TAGWERK = join(homedir(), '.local', 'bin', 'tagwerk');

export default function (pi: ExtensionAPI) {
  const beat = (_event: unknown, ctx: ExtensionContext) => {
    spawn(TAGWERK, ['beat', 'pi', '--cwd', ctx.cwd], { stdio: 'ignore' }).on('error', (err) =>
      ctx.ui.notify(`tagwerk: ${err.message}`, 'warning'),
    );
  };
  pi.on('session_start', beat);
  pi.on('turn_start', beat);
  pi.on('tool_execution_end', beat);
  pi.on('agent_settled', beat);
}
