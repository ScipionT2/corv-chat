# Nova — Website

The marketing/landing page for [Nova](https://github.com/escipionpedroza147-commits/Nova).

## Structure

```
website/
└── index.html    # Single-file landing page (inline CSS + JS)
```

Everything is self-contained in one HTML file — no build tools, no dependencies, no framework.

## Deploy

### GitHub Pages (Recommended)

1. Push the `website/` folder to your repo.
2. Go to **Settings → Pages** in your GitHub repo.
3. Under **Source**, select the branch (e.g., `main`) and set the folder to `/website`.
4. Click **Save**. Your site will be live at:  
   `https://escipionpedroza147-commits.github.io/Nova/`

> **Tip:** If you want the site at the root URL, move `index.html` to the repo root or use a custom `docs/` folder.

### Netlify

1. Connect your GitHub repo at [netlify.com](https://netlify.com).
2. Set **Publish directory** to `website`.
3. Deploy. Done.

### Vercel

1. Import the repo at [vercel.com](https://vercel.com).
2. Set **Root Directory** to `website`.
3. Deploy. No build command needed.

### Any Static Host

Just upload `index.html` to any web server or static host:

- **Cloudflare Pages** — Connect repo, set output dir to `website`
- **AWS S3 + CloudFront** — Upload `index.html` to an S3 bucket with static hosting enabled
- **Surge.sh** — `cd website && surge`
- **Python** (local preview) — `cd website && python3 -m http.server 8000`

## Local Preview

```bash
cd website
python3 -m http.server 8000
# → Open http://localhost:8000
```

## Customization

Everything is inline in `index.html`:

- **Colors** — Edit CSS custom properties in `:root { ... }`
- **Content** — Edit the HTML directly
- **Links** — Update GitHub URLs if the repo moves
- **OG Image** — Replace the `og:image` meta tag URL with your actual image

## License

Same as Nova — see the main repo's LICENSE file.
