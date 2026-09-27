# wine

A private, personal tool: pick a Systembolaget store, see every wine it stocks with its Vivino rating, and filter and sort instantly on a phone.

This repository holds the code only. The data it produces and the matching state stay private: the site runs behind Cloudflare Access, the state lives in a private repository, and the nightly run's Vivino data is cached only encrypted. Nothing here publishes wine data or ratings.

- `pipeline/`: fetches every store's wines, stock and shelf from Systembolaget's site API (at most 2 requests a second, shared across workers), matches each wine to Vivino (conservatively: an unconfirmed match shows no rating), refreshes ratings on a rolling schedule (about 1 request a second), and builds compact data files. See `pipeline/README.md`.
- `app/`: one static page, plain JS modules, no build step. See `app/README.md`.
- `.github/workflows/nightly.yml`: the nightly run.

Tests run offline against a mocked Systembolaget and Vivino: `python -m pipeline.tests.test_pipeline` (and `test_assortment`, `test_ratings`, `test_matching`, `test_net`).

Python 3.12; `pip install -r requirements.txt`.
