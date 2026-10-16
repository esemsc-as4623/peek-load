# Hosting the dashboard

The dashboard is a static site (`app/`, built with Vite + Preact) plus a folder of data files. There is no
API server and no database: whatever serves it only has to send files. Three ways to run it, from private to
public:

1. [**Local**](#1-local-on-your-own-computer): on your own computer, only you can see it.
2. [**Self-hosted**](#2-self-hosted-home-machine-with-tailscale-funnel): on a home machine, published with
   Tailscale Funnel; public, no domain or router changes needed. This is how it runs on C3PO.
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

Caddy serves the site on `localhost:2004` only (2004: Jean de Dieu Nkundabera's bronze at the Athens
Paralympics, Rwanda's first Paralympic medal), and [Tailscale Funnel](https://tailscale.com/kb/1223/funnel)
publishes that port on the public internet at `https://c3po.tailce5583.ts.net`, with Tailscale's certificate.
Ports 80 and 443 stay free for other services, and nothing else on the machine is exposed. The config is
[`infra/Caddyfile`](../infra/Caddyfile).

```
/srv/peek-load/
├── site/        ← app/dist after `npm run build` (index.html + assets/)
└── data/v1/     ← data/dashboard/app_v1
```

### Once

- Install Caddy (Ubuntu or Debian):
  ```bash
  sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
  sudo apt update && sudo apt install -y caddy
  ```
- In the Tailscale admin console: DNS → enable **MagicDNS** and **HTTPS Certificates**; Access controls →
  allow Funnel for this machine (`tailscale funnel` prints the link if it's missing).

### Deploy

From the repo root:

```bash
sudo mkdir -p /srv/peek-load/site /srv/peek-load/data/v1 && sudo chown -R $USER /srv/peek-load
cd app && npm ci && npm run build
rsync -a --delete dist/ /srv/peek-load/site/
rsync -a --delete ../data/dashboard/app_v1/ /srv/peek-load/data/v1/
sudo cp ../infra/Caddyfile /etc/caddy/Caddyfile && sudo systemctl restart caddy
curl -sI http://localhost:2004/ | head -1      # HTTP/1.1 200 OK
sudo tailscale funnel --bg 2004                # public: https://c3po.tailce5583.ts.net
tailscale funnel status
```

Funnel makes the site **public**: anyone with the address can open it. To share it only with devices on
your tailnet, use `sudo tailscale serve --bg 2004` instead of `funnel` (same address, tailnet only).
`sudo tailscale funnel --bg 2004 off` (or `serve … off`) takes it offline again.

To update, rebuild and rerun the two `rsync` lines; Caddy and Funnel keep running.

If Caddy won't start, `sudo journalctl -u caddy -n 50` says why; "listening on :80" means the default
Caddyfile is still in place and another service owns port 80, so copy `infra/Caddyfile` over it.

## 3. Server with a domain

### Before you start

- A server with a public IP, ports 80 and 443 open (`sudo ufw allow 80,443/tcp` if it runs a firewall).
- A domain (or sub-domain) whose DNS **A record** points at the server, e.g. `peekload.example.org`.
  Caddy needs this to get the certificate.
- Caddy installed as in section 2, and the folders created:
  `sudo mkdir -p /srv/peek-load/site /srv/peek-load/data/v1 && sudo chown -R $USER /srv/peek-load`.

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
