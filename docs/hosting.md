# Hosting the dashboard

The dashboard is a static site (`app/`, built with Vite + Preact) plus a folder of data files. There is no
API server and no database: a web server only has to send files. This guide sets up **Caddy** on a Linux
server (Ubuntu or Debian). Caddy gets and renews the HTTPS certificate on its own, which is why it's
recommended over nginx here.

```
/srv/peek-load/
├── site/        ← app/dist after `npm run build` (index.html + assets/)
└── data/v1/     ← data/dashboard/app_v1 (national.json, site.json, subdistrict_*.json, profiles.json, figures/)
```

The site asks for its data at `/data/v1/…` on the same server. To host the data elsewhere, build with
`VITE_DATA_BASE=https://data.example.org/v1 npm run build` (and allow that origin with a CORS header there).

## On a home machine with Tailscale Funnel (no domain, no router changes)

This is how the dashboard runs on C3PO. Caddy serves the site on `localhost:2004` only (2004: Jean de Dieu
Nkundabera's bronze at the Athens Paralympics, Rwanda's first Paralympic medal), and
[Tailscale Funnel](https://tailscale.com/kb/1223/funnel) publishes that port on the public internet at
`https://c3po.tailce5583.ts.net`, with Tailscale's certificate. Ports 80 and 443 stay free for other services,
and nothing else on the machine is exposed. The config is [`infra/Caddyfile`](../infra/Caddyfile).

Once, in the Tailscale admin console: DNS → enable **MagicDNS** and **HTTPS Certificates**; Access controls → allow
Funnel for this machine (`tailscale funnel` prints the link if it's missing). Then:

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

Funnel makes the site **public**: anyone with the address can open it. `sudo tailscale funnel --bg 2004 off`
takes it offline again. To update, rebuild and rerun the two `rsync` lines; Caddy and Funnel keep running.

## On a server with a domain

## 1. Before you start

- A server with a public IP, ports 80 and 443 open.
- A domain (or sub-domain) whose DNS **A record** points at the server, e.g. `peekload.example.org`.
  Caddy needs this to get the certificate.
- Node 18+ on the machine where you build (your laptop is fine; the server does not need Node).

## 2. Install Caddy (once)

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
sudo mkdir -p /srv/peek-load/site /srv/peek-load/data/v1
sudo chown -R $USER /srv/peek-load
```

If the server runs a firewall: `sudo ufw allow 80,443/tcp`.

## 3. Configure Caddy

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

Then `sudo systemctl reload caddy`. Check it with `sudo journalctl -u caddy -n 50` if anything fails.

## 4. Build and upload

From the repo, on your machine:

```bash
cd app
npm ci                 # first time only
npm test               # the renderer and every page against the local data
npm run build          # writes app/dist
rsync -av --delete dist/ USER@SERVER:/srv/peek-load/site/
rsync -av --delete ../data/dashboard/app_v1/ USER@SERVER:/srv/peek-load/data/v1/
```

Open `https://peekload.example.org`. Pages are addressed as `#/overview`, `#/sub-districts`, …, so no
server rewrite rules are needed.

When the data changes in a way that old pages can't read, upload it as `data/v2/` and build with
`VITE_DATA_BASE=/data/v2`, so cached copies never mix versions.

## Privacy & Distribution

The site has no download links or exports, the server does not list directories, and the rule above refuses
data files opened directly in the browser. Please do not try to save the data using developer tools. 
We have chosen to publish what we are content for people to browse, which is why the files hold aggregates 
and modelled values only (no survey microdata, no cluster locations, cells under 10 respondents suppressed).

## Licences on the public site

The site is non-commercial, as Meta's Relative Wealth Index (CC-BY-NC-4.0) requires. The footers credit
OpenStreetMap contributors (ODbL), the footprint providers, Copernicus and the other CC-BY sources.

## nginx instead of Caddy

If you prefer nginx: serve `/srv/peek-load/site` as the root, `location /data/ { alias /srv/peek-load/data/; }`,
`gzip on; gzip_types application/json;`, `autoindex off;`, and get a certificate with `certbot --nginx`.
