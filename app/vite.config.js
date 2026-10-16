import { defineConfig } from 'vite';
import preact from '@preact/preset-vite';
import { createReadStream, existsSync, statSync } from 'node:fs';
import { resolve, normalize } from 'node:path';

// In development the data is served from the repo's data folder (never committed);
// in production it lives on the server at VITE_DATA_BASE (default /data/v1).
const LOCAL_DATA = resolve(__dirname, '../data/dashboard/app_v1');

function localData() {
  return {
    name: 'local-data',
    configureServer(server) { server.middlewares.use('/data/v1', serve); },
    configurePreviewServer(server) { server.middlewares.use('/data/v1', serve); }
  };
}

function serve(req, res, next) {
  const p = normalize(resolve(LOCAL_DATA, '.' + decodeURIComponent(req.url.split('?')[0])));
  if (!p.startsWith(LOCAL_DATA) || !existsSync(p) || !statSync(p).isFile()) return next();
  res.setHeader('Content-Type', p.endsWith('.json') ? 'application/json' : p.endsWith('.png') ? 'image/png' : 'application/octet-stream');
  createReadStream(p).pipe(res);
}

export default defineConfig({
  base: process.env.VITE_BASE || '/',
  plugins: [preact(), localData()],
  build: { outDir: 'dist', sourcemap: false }
});
