# Vendored front-end libraries

Every third-party script, stylesheet and font the templates use is served
from here instead of a CDN, so the Content-Security-Policy can allow
scripts from `'self'` only (see `src/functions/security_headers.py`) and
no outside host can change what runs on the site. Each folder name
includes the exact version.

| Folder | Package | Files taken from |
|---|---|---|
| `bootstrap-5.3.3` | bootstrap 5.3.3 | `dist/css/bootstrap.min.css`, `dist/js/bootstrap.bundle.min.js` |
| `chartjs-4.6.1` | chart.js 4.6.1 | `dist/chart.umd.min.js` |
| `chartjs-adapter-date-fns-3.0.0` | chartjs-adapter-date-fns 3.0.0 | `dist/chartjs-adapter-date-fns.bundle.min.js` |
| `chartjs-plugin-zoom-2.0.1` | chartjs-plugin-zoom 2.0.1 | `dist/chartjs-plugin-zoom.min.js` |
| `fontawesome-free-6.7.2` | @fortawesome/fontawesome-free 6.7.2 | `css/all.min.css`, `css/v4-shims.min.css`, `webfonts/*.woff2` |
| `hammerjs-2.0.8` | hammerjs 2.0.8 | `hammer.min.js` |
| `htmx-2.0.3` | htmx.org 2.0.3 | `dist/htmx.min.js` |
| `jquery-3.7.1` | jquery 3.7.1 | `dist/jquery.min.js` |
| `mdb-ui-kit-9.3.0` | mdb-ui-kit 9.3.0 | `css/mdb.min.css`, `js/mdb.umd.min.js` |
| `roboto-5.3.0` | @fontsource/roboto 5.3.0 | `files/roboto-latin-{300,400,500,700}-normal.woff2`; `roboto.css` is hand-written |
| `trumbowyg-2.27.3` | trumbowyg 2.27.3 | `dist/trumbowyg.min.js`, `dist/ui/trumbowyg.min.css`, `dist/ui/icons.svg`, `dist/plugins/{preformatted,noembed,upload}/*.min.js` |

All were downloaded from `https://cdn.jsdelivr.net/npm/<package>@<version>/<file>`.
Font Awesome replaced the `kit.fontawesome.com` kit, which was the same
free 6.7.2 set with the v4 shims turned on. Only the `.woff2` webfonts are
kept: every browser that runs this site's JavaScript supports them, so
the `.ttf` fallbacks in `all.min.css` are never requested.

## Upgrading a library

1. Download the new version's files into a new folder named
   `<library>-<version>` (keep the same file layout).
2. Point the templates at the new folder (`grep -rn 'vendor/<library>-' templates`).
3. Delete the old folder and regenerate the checksums:
   `cd static/vendor && find . -type f ! -name README.md ! -name SHA256SUMS | sort | xargs sha256sum > SHA256SUMS`.
4. Update the table above.

`sha256sum -c SHA256SUMS` (run in this folder) checks that nothing has
changed since the files were downloaded.
