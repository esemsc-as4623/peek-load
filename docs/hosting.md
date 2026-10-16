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
peekload.example.org {
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

## About "not downloadable"

The site has no download links or exports, the server does not list directories, and the rule above refuses
data files opened directly in the browser. That keeps casual downloading off the site. It cannot stop a
determined visitor: anything a browser draws (the JSON behind the maps, the figures) has been sent to that
browser and can be saved from its developer tools. Only publish what you are content for people to have,
which is why the files hold aggregates and modelled values only (no survey microdata, no cluster locations,
cells under 10 respondents suppressed).

## Licences on the public site

The site is non-commercial, as Meta's Relative Wealth Index (CC-BY-NC-4.0) requires. The footers credit
OpenStreetMap contributors (ODbL), the footprint providers, Copernicus and the other CC-BY sources; keep
them when changing pages.

## nginx instead of Caddy

If you prefer nginx: serve `/srv/peek-load/site` as the root, `location /data/ { alias /srv/peek-load/data/; }`,
`gzip on; gzip_types application/json;`, `autoindex off;`, and get a certificate with `certbot --nginx`.
