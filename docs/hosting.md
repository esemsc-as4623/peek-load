# Hosting the dashboard

The dashboard is a static site (`app/`, built with Vite + Preact) plus a folder of data files. There is no
API server and no database: whatever serves it only has to send files. Three ways to run it, from private to
public:

1. [**Local**](#1-local-on-your-own-computer): on your own computer, only you can see it.
2. [**Self-hosted**](#2-self-hosted-home-machine-with-tailscale-funnel): on a home machine, as its own
   Tailscale machine published with Funnel at `https://peek-load-rwa.tailce5583.ts.net`; public, no domain or
   router changes needed. This is how it runs on C3PO.
3. [**Server with a domain**](#3-server-with-a-domain): on a server with ports 80 and 443 open.

All three need **Node 18+** on the machine that builds the site, and the data folder
`data/dashboard/app_v1/` (national.json, site.json, subdistrict_*.json, profiles.json, figures/). The data is
never committed; `app/README.md` explains how it is refreshed from the design canvas.

## 1. Local: on your own computer

From the repo:

```bash
cd app
npm ci              # first time only
npm test            # the renderer and every page against the local data
npm run dev         # http://localhost:5173, reloads as you edit
```

To see exactly what will be published (the production build):

```bash
npm run build && npm run preview     # http://localhost:4173
```

Both serve the data from `data/dashboard/app_v1/` at `/data/v1/`, so nothing needs copying. They listen on
`localhost` only: no one else can open them. Stop either with Ctrl+C.

## 2. Self-hosted: home machine with Tailscale Funnel

The dashboard runs as its own Tailscale machine, **`peek-load-rwa`**, published with
[Tailscale Funnel](https://tailscale.com/kb/1223/funnel) at **`https://peek-load-rwa.tailce5583.ts.net`**,
with Tailscale's certificate. Two Docker containers do it ([`infra/dashboard/compose.yaml`](../infra/dashboard/compose.yaml)):

- `ts` joins the tailnet as `peek-load-rwa` and funnels HTTPS to the web server;
- `web` is Caddy with [`infra/Caddyfile`](../infra/Caddyfile), on port 2004 (Jean de Dieu Nkundabera's
  bronze at the Athens Paralympics, Rwanda's first Paralympic medal), inside the `ts` container's network.

So the host machine keeps its own name, opens no ports, and none of its other services are exposed. The files
are served from:

```
/srv/peek-load/
├── site/        ← app/dist after `npm run build` (index.html + assets/)
└── data/v1/     ← data/dashboard/app_v1
```

### Once

- In the Tailscale admin console:
  - DNS → enable **MagicDNS** and **HTTPS Certificates**;
  - Access controls → allow Funnel (the default policy grants the `funnel` attribute to `autogroup:member`);
  - Settings → Keys → **Generate auth key** (not reusable, not ephemeral).
- Put the key in a local `.env` (git-ignored):
  ```bash
  cp infra/dashboard/.env.example infra/dashboard/.env   # then paste the key after TS_AUTHKEY=
  ```
- `sudo mkdir -p /srv/peek-load/site /srv/peek-load/data/v1 && sudo chown -R $USER /srv/peek-load`

### Deploy

From the repo root:

```bash
cd app && npm ci && npm test && npm run build && cd ..
rsync -a --delete app/dist/ /srv/peek-load/site/
rsync -a --delete data/dashboard/app_v1/ /srv/peek-load/data/v1/
docker compose -f infra/dashboard/compose.yaml up -d
docker compose -f infra/dashboard/compose.yaml logs -f ts     # wait for the machine to come up; Ctrl+C
```

Open `https://peek-load-rwa.tailce5583.ts.net` (the first visit can take a minute while the certificate is
issued). The site is **public**: anyone with the address can open it.

- **Update:** rebuild and rerun the two `rsync` lines; the containers serve the new files at once.
- **Take it offline:** `docker compose -f infra/dashboard/compose.yaml down` (`up -d` brings it back; the
  machine keeps its name, so the key is not needed again).
- **Tailnet only, not public:** set `"AllowFunnel"` to `{}` in `infra/dashboard/serve.json` and run `up -d`.
- **Logs:** `docker compose -f infra/dashboard/compose.yaml logs ts web`. If the name comes up as
  `peek-load-rwa-1`, an older machine with that name exists: delete it in the admin console and restart.

### Moving from the earlier setup (Caddy on the host, Funnel from C3PO)

Once `https://peek-load-rwa.tailce5583.ts.net` works, switch the old address off:

```bash
sudo tailscale funnel --bg 2004 off      # c3po.tailce5583.ts.net stops serving the dashboard
sudo systemctl disable --now caddy       # the host Caddy is no longer needed
```

## 3. Server with a domain

### Before you start

- A server with a public IP, ports 80 and 443 open (`sudo ufw allow 80,443/tcp` if it runs a firewall).
- A domain (or sub-domain) whose DNS **A record** points at the server, e.g. `peekload.example.org`.
  Caddy needs this to get the certificate.
- Caddy installed (Ubuntu or Debian):
  ```bash
  sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
  sudo apt update && sudo apt install -y caddy
  sudo mkdir -p /srv/peek-load/site /srv/peek-load/data/v1 && sudo chown -R $USER /srv/peek-load
  ```

### Configure Caddy

Replace the contents of `/etc/caddy/Caddyfile` with (change the domain):

```caddy
peekload.rwa.org {
	encode zstd gzip

	# data: no directory listings, long cache (the folder is versioned: v1, v2, ...)
	handle /data/* {
		root * /srv/peek-load
		# refuse a data file typed into the address bar; the page's own requests still work
		@typed header Sec-Fetch-Dest document
		respond @typed 403
		header Cache-Control "public, max-age=604800"
		file_server
	}

	# the site
	handle {
		root * /srv/peek-load/site
		@page path / /index.html
		header @page Cache-Control "no-cache"
		header /assets/* Cache-Control "public, max-age=31536000, immutable"
		file_server
	}

	header {
		X-Content-Type-Options nosniff
		Referrer-Policy strict-origin-when-cross-origin
	}
}
```

Then `sudo systemctl reload caddy`. Caddy gets and renews the HTTPS certificate on its own.

### Build and upload

From the repo, on your machine (the server does not need Node):

```bash
cd app
npm ci && npm test && npm run build
rsync -av --delete dist/ USER@SERVER:/srv/peek-load/site/
rsync -av --delete ../data/dashboard/app_v1/ USER@SERVER:/srv/peek-load/data/v1/
```

Open `https://peekload.example.org`.

### nginx instead of Caddy

Serve `/srv/peek-load/site` as the root, `location /data/ { alias /srv/peek-load/data/; }`,
`gzip on; gzip_types application/json;`, `autoindex off;`, and get a certificate with `certbot --nginx`.

## Notes for every option

**Addresses.** Pages are addressed as `#/overview`, `#/sub-districts`, …, so no server rewrite rules are
needed. The site asks for its data at `/data/v1/…` on the same host. To host the data elsewhere, build with
`VITE_DATA_BASE=https://data.example.org/v1 npm run build` (and allow that origin with a CORS header there).

**Data versions.** When the data changes in a way that old pages can't read, upload it as `data/v2/` and
build with `VITE_DATA_BASE=/data/v2`, so cached copies never mix versions.

**Privacy & distribution.** The site has no download links or exports, the server does not list directories,
and the Caddy configs refuse data files opened directly in the browser. Please do not try to save the data using
developer tools. We have chosen to publish what we are content for people to browse, which is why the files hold
aggregates and modelled values only (no survey microdata, no cluster locations, cells under 10 respondents
suppressed).

**Licences.** The site is non-commercial, as Meta's Relative Wealth Index (CC-BY-NC-4.0) requires. The
footers credit OpenStreetMap contributors (ODbL), the footprint providers, Copernicus and the other CC-BY
sources.
