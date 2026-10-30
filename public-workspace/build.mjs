import { execFileSync } from 'node:child_process';
import { cpSync, readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';

const frontend = fileURLToPath(new URL('../frontend/', import.meta.url));
const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm';
execFileSync(npm, ['run', 'build'], { cwd: frontend, stdio: 'inherit', shell: process.platform === 'win32',
  env: { ...process.env, VITE_PUBLIC_DEMO: 'true', VITE_API_BASE_URL: '/api' } });
const manifest = JSON.parse(readFileSync(new URL('../frontend/src/demo/assets.json', import.meta.url), 'utf8'));
for (const asset of manifest) {
  const bytes = readFileSync(new URL(`../frontend/dist${asset.path}`, import.meta.url));
  if (bytes.subarray(0, 5).toString() !== '%PDF-' || bytes.length !== asset.bytes
    || createHash('sha256').update(bytes).digest('hex') !== asset.sha256) throw new Error(`Source document missing or changed: ${asset.path}`);
}
cpSync(new URL('../frontend/dist/', import.meta.url), new URL('./dist/', import.meta.url), { recursive: true });
