# Project website

A static, single-page site: `index.html`, `assets/` (CSS and JS), `data/site.json`, `img/`, and
`videos/` (captioned room clips with poster frames). Charts use Observable Plot and d3 from
jsDelivr (pinned versions with integrity hashes); everything else is local.

## Edit and preview

```bash
python3 scripts/build_site_data.py              # rebuild data/site.json after changing inputs
python3 -m http.server 8090 --directory site    # then open http://localhost:8090
```

`data/site.json` is built from `analysis/figures/solved_by_rung.csv`,
`harness/config/ladder.json`, the caption timelines in `site/edl/`, and the headline counts at
the top of `scripts/build_site_data.py`. Open the page through a web server, not `file://`.

The introduction is a placeholder (`#intro` in `index.html`). Photos go in `img/`; strip their
metadata first (e.g. re-encode with `ffmpeg -map_metadata -1`).

## Videos

Clips are cut from the OBS room recording and captioned from the agents' own logs:

1. `scripts/auto_edl.py artifacts/trials/<trial>` drafts a caption timeline (EDL) from both
   agents' logs; review and trim it by hand.
2. On the machine with the recordings and ffmpeg (lab1): `scripts/cut_clips.py` cuts the raw
   clip, and `scripts/make_reel.py` renders the captioned clip from the EDL.
3. Copy the clip to `site/videos/`, the EDL to `site/edl/`, add a poster frame
   (`<trial>.jpg`), and list it in `VIDEOS` in `scripts/build_site_data.py`.

The highlight reel at the top (`videos/highlights.mp4`) is built from those captioned clips by
`scripts/make_highlight_reel.py`: title cards, voices and tones at normal speed, quiet stretches sped
up with an on-screen speed label. Edit the `REEL` list there to change the cut.

Clips from `e3p*` trials are from the pilot runs, before the sound-server fix; the page labels
them "pilot".

## When the paper is ready

1. Put the PDF at `site/paper.pdf`.
2. In `index.html`, replace `<span class="muted">Paper (PDF coming soon)</span>` with
   `<a href="paper.pdf">Paper (PDF)</a>`.
3. In the Cite section, put the paper's BibTeX inside `<pre id="bib-paper">` (remove the
   `bib-empty` class) and remove `disabled` from its Copy button.
4. Push to `main`; the deploy picks it up.

## Deploy

Pushing a change under `site/` to `main` uploads the site to theagentsaretalking.com on Hostinger
over FTP (`.github/workflows/deploy-site.yml`, same approach as haltriedman.com). The workflow
needs repo secrets `FTP_SERVER`, `FTP_USERNAME` and `FTP_PASSWORD`, and the domain must be added
as a website in hPanel. It only uploads; files removed from `site/` must be deleted on the server
by hand.

The site is behind HTTP basic auth (`site/.htaccess`). The password hash is
`deploy/theagentsaretalking.htpasswd` (APR1), uploaded outside `public_html`. To change the
password: `printf 'USER:%s\n' "$(openssl passwd -apr1 'NEW_PASSWORD')" > deploy/theagentsaretalking.htpasswd`
and push. Pages are also marked `noindex` and `Cache-Control: private`.
