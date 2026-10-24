# yfinance 0.2.66 to 1.7.0: upgrade report

Prepared 2026-10-24. Covers every release from 0.2.66 (2025-09-17) through 1.7.0 (2026-08-26),
the current PyPI release: 1.0, 1.1.0, 1.2.0, 1.2.1, 1.2.2, 1.3.0, 1.4.0, 1.4.1, 1.5.1 (1.5.0 was
retracted), 1.5.2, 1.6.0, 1.7.0.

Sources: [`CHANGELOG.rst`](https://github.com/ranaroussi/yfinance/blob/main/CHANGELOG.rst), the
[GitHub releases](https://github.com/ranaroussi/yfinance/releases), the linked PRs, and the 1.7.0
source as installed (`.venv/Lib/site-packages/yfinance`), compared against the 0.2.66 and 1.6.0
wheels. PR numbers below link to `ranaroussi/yfinance`. Where a claim comes from reading the
source rather than a changelog line, it says so and names the file.

Lock changes from the upgrade: `yfinance` 0.2.66 to 1.7.0, `curl-cffi` 0.13.0 to 0.16.3, `lxml`
6.1.3 added, `frozendict` dropped. `pandas` stays at 2.3.3.

## New features

| Release | Feature | Ref |
|---|---|---|
| 1.0 | Global config object `yf.config` (`network.proxy`, `network.retries`, `debug.hide_exceptions`, `debug.logging`); replaces per-call `proxy=` and `history(raise_errors=)`, which now warn | [#2524](https://github.com/ranaroussi/yfinance/pull/2524) |
| 1.0 | Opt-in retry of transient network errors: `yf.config.network.retries = N` (exponential backoff, default 0) | [#2627](https://github.com/ranaroussi/yfinance/pull/2627) |
| 1.0 | Earnings and other calendars (`yf.Calendars`) | [#2615](https://github.com/ranaroussi/yfinance/pull/2615) |
| 1.1.0 | Price repair: capital-gains double-counting | [#2667](https://github.com/ranaroussi/yfinance/pull/2667) |
| 1.2.0 | Updated equity and mutual-fund exchange maps (screener) | [#2685](https://github.com/ranaroussi/yfinance/pull/2685) |
| 1.2.1 / 1.2.2 | Dividends keep their currency | [#2695](https://github.com/ranaroussi/yfinance/pull/2695) |
| 1.2.2 | Currency on analysis data | [#2739](https://github.com/ranaroussi/yfinance/pull/2739) |
| 1.3.0 | Valuation-measures table (`Ticker.get_valuation_measures`) | [#2742](https://github.com/ranaroussi/yfinance/pull/2742) |
| 1.3.0 | `ETFQuery` for the screener | [#2749](https://github.com/ranaroussi/yfinance/pull/2749) |
| 1.4.0 | `yf.Auth` to log in to a Yahoo account | [#2761](https://github.com/ranaroussi/yfinance/pull/2761) |
| 1.4.0 | Region scoping for `Sector`/`Industry`; `lang`/`region` scoping for `Ticker` (`yf.config.locale`) | [#2803](https://github.com/ranaroussi/yfinance/pull/2803), [#2804](https://github.com/ranaroussi/yfinance/pull/2804) |
| 1.4.0 | `curl_cffi` optional, falling back to `requests` | [#2802](https://github.com/ranaroussi/yfinance/pull/2802) |
| 1.5.1 | Fundamentals fetch falls back to chunked requests after a time-out | [#2811](https://github.com/ranaroussi/yfinance/pull/2811) |
| 1.5.1 | Valuation measures from the timeseries API instead of an HTML scrape | [#2851](https://github.com/ranaroussi/yfinance/pull/2851) |
| 1.6.0 | Balance-sheet keys; screener dividend-yield fields | [#2879](https://github.com/ranaroussi/yfinance/pull/2879), [#2888](https://github.com/ranaroussi/yfinance/pull/2888) |
| 1.7.0 | `history_metadata` lazy-loads `tradingPeriods` | [#2922](https://github.com/ranaroussi/yfinance/pull/2922) |

## Fixes

Those that touch what folio calls (`fast_info`, `Ticker.info`, `download`, `history`):

- **Thread safety of `download()`**: mutex lock (1.2.1/1.2.2, [#2718](https://github.com/ranaroussi/yfinance/pull/2718)), then reentrant with no shared module globals (1.4.0, [#2805](https://github.com/ranaroussi/yfinance/pull/2805)). folio runs `download()` only from the main thread, but this removes a class of cross-call corruption.
- **`history()` on a `None`/malformed chart response** no longer raises `TypeError` (1.2.2 [#2703](https://github.com/ranaroussi/yfinance/pull/2703), 1.4.0 [#2794](https://github.com/ranaroussi/yfinance/pull/2794), extra guard [#2778](https://github.com/ranaroussi/yfinance/pull/2778)).
- **`Ticker.info` on a null API result** handled (1.5.1 [#2869](https://github.com/ranaroussi/yfinance/pull/2869), 1.6.0 [#2906](https://github.com/ranaroussi/yfinance/pull/2906)). folio's `_describe` already catches everything, so this only turns a logged debug miss into an empty dict.
- **Dividends on unlisted tickers** (1.4.0 [#2797](https://github.com/ranaroussi/yfinance/pull/2797)).
- **`download()` index name** preserved as `Date`/`Datetime` (1.4.1 [#2832](https://github.com/ranaroussi/yfinance/pull/2832)).
- **"possibly delisted"** no longer claimed when Yahoo gives a reason (1.6.0 [#2903](https://github.com/ranaroussi/yfinance/pull/2903)); error text for `30m` no longer shows `15m` ([#2900](https://github.com/ranaroussi/yfinance/pull/2900)).
- **Proxy and cookie robustness** (1.7.0 [#2953](https://github.com/ranaroussi/yfinance/pull/2953)): a session's own proxy is no longer wiped on every request; an `fc.yahoo.com` time-out and a getcrumb 429 or time-out now degrade to a crumb-less request instead of failing the call; a failed crumb refetch no longer sends `crumb=None`.
- **Login cookies** survive cookie-strategy switches (1.5.1 [#2850](https://github.com/ranaroussi/yfinance/pull/2850)); proxy strings normalised ([#2853](https://github.com/ranaroussi/yfinance/pull/2853)).
- **pandas 3 and numpy 2.5**: pandas 3.0 support (1.1.0 [#2683](https://github.com/ranaroussi/yfinance/pull/2683)), read-only array fixes (1.2.0 [#2697](https://github.com/ranaroussi/yfinance/pull/2697), 1.6.0 [#2897](https://github.com/ranaroussi/yfinance/pull/2897)), Timedelta warnings (1.6.0 [#2891](https://github.com/ranaroussi/yfinance/pull/2891), [#2915](https://github.com/ranaroussi/yfinance/pull/2915)).
- **curl_cffi**: blocked 0.14 (1.0 [#2653](https://github.com/ranaroussi/yfinance/pull/2653)), forced >=0.15 for a CVE (1.2.1 [#2743](https://github.com/ranaroussi/yfinance/pull/2743)), fixed for >=0.16 (1.5.2).
- **Price repair**: many fixes across 1.1.0 to 1.7.0 ([#2668](https://github.com/ranaroussi/yfinance/pull/2668), [#2842](https://github.com/ranaroussi/yfinance/pull/2842), [#2843](https://github.com/ranaroussi/yfinance/pull/2843), [#2859](https://github.com/ranaroussi/yfinance/pull/2859), [#2860](https://github.com/ranaroussi/yfinance/pull/2860), [#2907](https://github.com/ranaroussi/yfinance/pull/2907), [#2908](https://github.com/ranaroussi/yfinance/pull/2908), [#2943](https://github.com/ranaroussi/yfinance/pull/2943), [#2958](https://github.com/ranaroussi/yfinance/pull/2958)). folio never passes `repair=True`, so none of these apply.

## Breaking changes

The 1.0 release notes say "no breaking changes, but some deprecation warnings". In practice:

1. **Deprecated, still working**: per-call `proxy=` and `history(raise_errors=...)` emit `DeprecationWarning` in favour of `yf.config` (1.0, [#2524](https://github.com/ranaroussi/yfinance/pull/2524)). folio uses neither.
2. **`history()` frames are consolidated** and can come back read-only, so in-place writes into a returned frame may raise (1.2.0 release note). folio only reads (`dropna`, `to_numpy`, `tolist`), so no impact.
3. **Intraday `download()` index timezone**: with the intraday default `ignore_tz=False`, the index is now in the most common exchange timezone instead of always UTC (1.4.0, [#2825](https://github.com/ranaroussi/yfinance/pull/2825); contract documented in 1.6.0 [#2936](https://github.com/ranaroussi/yfinance/pull/2936)). folio's `_dated_closes(intraday=True)` converts an aware stamp with `astimezone(UTC)`, so the stored instants are unchanged.
4. **`download()` no longer raises per-ticker errors into its collector.** 0.2.66 passed `raise_errors=True` to each ticker's `history()`; 1.x instead toggles `yf.config.network.hide_exceptions`, a key nothing reads (source, `multi.py` `_download_one`; reported in open [#2994](https://github.com/ranaroussi/yfinance/pull/2994)). A failed ticker still comes back as empty/NaN columns, which is what folio reads, so no impact.
5. **Dependencies**: `frozendict` dropped ([#2821](https://github.com/ranaroussi/yfinance/pull/2821)), `curl_cffi>=0.15` required ([#2743](https://github.com/ranaroussi/yfinance/pull/2743)), packaging moved to `pyproject.toml` (1.6.0 [#2920](https://github.com/ranaroussi/yfinance/pull/2920)).
6. **1.5.0 was retracted**: never pin to it.

### Seams folio relies on (checked offline against 1.7.0)

- `yf.Tickers("MSFT RY.TO spy").tickers` is still a `dict` keyed by upper-cased symbol, values `Ticker`.
- `FastInfo` still exposes `last_price`, `regular_market_previous_close`, `previous_close` and `currency` as properties; `Ticker.fast_info` is a `FastInfo`.
- `Ticker.info` is still a property. The keys folio reads pass through unchanged: `info_retired_keys` in `scrapers/quote.py` is defined but never used to strip anything.
- `yf.download(..., group_by="ticker", timeout=...)` still accepts every keyword folio passes and keeps the `(symbol, field)` MultiIndex for one symbol as well as several; daily index named `Date`, tz-naive.

## Known issues in 1.7.0

1. **A rate-limited getcrumb response is cached as the crumb.** Source, `data.py`: `_get_crumb_basic` and `_get_crumb_csrf` assign `self._crumb = response.text` *before* checking for 429, then raise `YFRateLimitError`. The next request finds `_crumb` set and sends `crumb=Too Many Requests` (or similar), and a crumb-checked endpoint answers 401. I found no GitHub issue for it. This bug is **not new in 1.7.0**: the same assign-then-check is in 0.2.66 and 1.6.0. What 1.7.0 changed ([#2953](https://github.com/ranaroussi/yfinance/pull/2953)) is that the first 429 no longer fails the request: it is sent crumb-less, so the poisoned crumb is first *used* by a later request instead of the caller seeing an exception. It recovers on its own: any response `>= 400` toggles the cookie strategy, which clears `_crumb`, re-mints it and retries once. The crumb lives only in memory (`cache.py` persists cookies, never the crumb), so it cannot outlive one folio run. See the upgrade decision at the end.
2. **One unknown symbol resets the crumb for every thread.** Any `>= 400`, including the 404 a delisted ticker gets, toggles the cookie strategy and wipes the crumb the `YfData` singleton shares across threads (open [#2994](https://github.com/ranaroussi/yfinance/pull/2994), item 2). folio's eight-worker `fast_info` and `.info` fan-out then re-mints the crumb several times, which is exactly what invites the getcrumb 429 in issue 1.
3. **Every `download()` without `session=` swaps a fresh session into the singleton** but keeps the old crumb, so the next crumb-checked request goes out with an empty cookie jar and an old crumb (source: `multi.py` calls `YfData(session=new_session())`; `_set_session` does not reset `_crumb`; open [#2994](https://github.com/ranaroussi/yfinance/pull/2994), item 4). folio's `_fetch_history` makes four downloads and `_fetch_intraday` two.
4. **`cache_get` has no expiry.** Requests for a date range wholly in the past are `lru_cache`d for the process (open [#2994](https://github.com/ranaroussi/yfinance/pull/2994), item 1). folio's downloads pass `start=` with no `end`, so they never take that path.
5. **Extra time-zone request per symbol** for `start=`/`period="max"` history on a cold tz cache (open [#2976](https://github.com/ranaroussi/yfinance/pull/2976)). folio's history refetch hits this for every new symbol.
6. **Weekly live bar merged into the previous week** when a holiday moves the first bar to Tuesday (open [#3013](https://github.com/ranaroussi/yfinance/pull/3013)). folio's weekly `1wk` closes could show this around holiday weeks. Related fix [#2985](https://github.com/ranaroussi/yfinance/pull/2985) (index `freq` for `1wk`/`1mo`/`3mo`) is merged to `dev`, unreleased.
7. **`dividends`/`splits` return `None`** instead of an empty Series after a failed price fetch (fixed on `dev` by [#2977](https://github.com/ranaroussi/yfinance/pull/2977), unreleased). folio does not read these.
8. **Brotli CVE in curl_cffi's bundled library** (open [#3012](https://github.com/ranaroussi/yfinance/issues/3012)): reported against yfinance 1.7.0 with curl_cffi 0.16.3, the version this lock now carries. Nothing to do in folio until curl_cffi ships a fix; worth watching.
9. **pandas 3.1 `inplace` deprecation warnings** (open [#3009](https://github.com/ranaroussi/yfinance/issues/3009)). folio is on pandas 2.3.3, so not yet.
10. Data-side: a missing 2026-08-28 daily session across US tickers ([#2964](https://github.com/ranaroussi/yfinance/issues/2964), closed as a Yahoo data gap); `get_news()` returns `[]` ([#3004](https://github.com/ranaroussi/yfinance/issues/3004)). Neither touches folio.

## Worth adopting in folio-updater

Ranked by what they buy. None is adopted in this change.

1. **Let a rate limit out of `fast_info`.** *Touches* `_attr` and `_price` in `_fetch_prices` (`src/services/quotes_service.py`). Today `_attr` swallows every exception, and `fast_info.last_price` raises `YFRateLimitError` on a 429 (`history()` re-raises it as a "special case for rate limits", `scrapers/history.py`; [#2524](https://github.com/ranaroussi/yfinance/pull/2524)). So `_fetch_prices` never raises in practice, the `_fetch_daily` throttling fallback in `refresh` is unreachable, and a partially throttled run negative-caches the throttled symbols as `NOT_FOUND` for seven days. Re-raising `yfinance.exceptions.YFRateLimitError` from `_attr` would make the fallback work as designed and stop false not-found rows. Smallest, highest-value change.
2. **Raise instead of infer outages: `yf.config.debug.hide_exceptions = False`.** *Touches* the yfinance import site(s) in `quotes_service.py`, then `refresh` (the "nothing priced but some priced before" outage guess), `_attr`, `_describe`, `_refetch_history`, `intraday`. Since 1.0 ([#2524](https://github.com/ranaroussi/yfinance/pull/2524)) this global makes `history()`, and so `fast_info`, raise the real network error instead of returning an empty frame. *Buys*: an outage becomes an exception the existing `except Exception` in `refresh` already handles, and "unknown symbol" (`YFPricesMissingError`/`YFTzMissingError`, both `YFTickerMissingError`) becomes distinguishable from "request failed", so `NOT_FOUND` is written only for the former. *Caveats*: it is process-global, and it does not reach inside `download()` (known issue 4), so the history paths keep reading empty frames. Needs tests for the new exception split.
3. **No caller timeout exists for `fast_info`, so keep the probe.** In 1.7.0 the chart request behind `fast_info` uses `history()`'s default `timeout=10` with no way to pass one through `fast_info`, and the cookie and crumb fetches use hard-coded 30 s (`_make_request` calls `_get_cookie_and_crumb()` with no timeout; `data.py`). Nothing in 0.2.66 to 1.7.0 adds a config-level timeout ([#2524](https://github.com/ranaroussi/yfinance/pull/2524) config has only `proxy`, `retries`, `hide_exceptions`, `logging`, `locale`). Options if `quotes.timeout_seconds` must bind: replace `fast_info` with `ticker.history(period="5d", interval="1d", timeout=...)` plus `get_history_metadata()` for currency and the regular-session previous close, which bounds the data request but still not the 30 s crumb. *Touches* `_fetch_prices`, `_previous_close`. *Buys* a bounded per-symbol wait. The `_provider_reachable` probe remains the real fail-fast for an unreachable host.
4. **Share one session across all calls.** *Touches* `_fetch_prices` (`yf.Tickers(..., session=)`), `_fetch_metadata` (`yf.Ticker(..., session=)`), `_download` and `_fetch_daily` (`yf.download(..., session=)`). Passing one `curl_cffi` session (`curl_cffi.requests.Session(impersonate="chrome")`; yfinance's own `new_session()` lives in the private `yfinance._http`) avoids known issue 3: each `download()` otherwise replaces the session and strands the crumb. *Buys* fewer 401-then-retry round-trips during `update`, which makes fewer getcrumb calls and less chance of the 429 in known issue 1. It is also where a proxy or custom CA would go if one is ever needed (1.7.0 stops wiping session proxies, [#2953](https://github.com/ranaroussi/yfinance/pull/2953)).
5. **Retries: leave `network.retries` at 0.** [#2627](https://github.com/ranaroussi/yfinance/pull/2627) retries only the target request, not the cookie or crumb fetch, with backoff `2**attempt`. For folio it would lengthen exactly the hangs the probe was built to avoid. Mention only so nobody turns it on to "fix" flakiness.
6. **Batch quotes: none to adopt.** No release from 0.2.66 to 1.7.0 adds a multi-symbol quote call. `v7/finance/quote` is used only per symbol inside `Ticker.info` (`scrapers/quote.py`), and `download()` still issues one chart request per symbol on a thread pool. The per-symbol `fast_info` fan-out in `_fetch_prices` stays. `yf.WebSocket`/`Tickers.live` (predate 0.2.66) stream live prices but only during sessions, so they do not fit a cache refresh.
7. **Dividend currency.** `Ticker.dividends` now keeps the dividend's currency (1.2.1/1.2.2 [#2695](https://github.com/ranaroussi/yfinance/pull/2695)). *Touches* `_fetch_metadata` if folio ever moves `last_dividend`/`dividend_rate` from `.info` to the dividends series, and the `Fundamentals` fields. *Buys* correct units for cross-listed holdings (a US-dollar dividend on a TSX listing).
8. **Cheaper fund and valuation data.** `get_valuation_measures` (1.3.0 [#2742](https://github.com/ranaroussi/yfinance/pull/2742), moved to the timeseries API in 1.5.1 [#2851](https://github.com/ranaroussi/yfinance/pull/2851)) and the existing `get_funds_data()` could fill `trailing_pe`/`forward_pe` and the fund fields (`expense_ratio`, `total_assets`, `category`, `fund_family`). *Touches* `_fetch_metadata`. *Buys* little today: `.info` already supplies these in the same request, so it would only matter if `.info` keys start disappearing.
9. **Locale.** `yf.config.locale.region` (1.4.0 [#2804](https://github.com/ranaroussi/yfinance/pull/2804)) scopes `Ticker` requests by region. For a mostly-Canadian folio, `region = "CA"` might give Canadian `shortName`s and exchange names in `.info`. *Touches* the import site in `quotes_service.py`. Unverified: try it on a few `.TO` symbols before adopting.
10. **History time-zone request.** When [#2976](https://github.com/ranaroussi/yfinance/pull/2976) ships, `_fetch_history` halves its cold-cache requests with no folio change. Nothing to do but upgrade.

## Upgrade decision

`pyproject.toml` floor raised to `yfinance>=1.7.0`; the lock resolves 1.7.0. No guard and no pin for the crumb bug (known issue 1), because:

- **Pinning cannot avoid it.** The poisoned crumb is cached the same way in 0.2.66 and 1.6.0; 1.7.0 only changed what the first 429 does.
- **1.7.0 is better for folio on that first 429.** Before, a getcrumb 429 raised `YFRateLimitError` out of `fast_info`, `_attr` swallowed it, and the symbol was negative-cached as `NOT_FOUND`. Now the chart request goes out crumb-less and usually prices.
- **The blast radius is one run's metadata.** A poisoned crumb draws a 401, yfinance toggles the strategy, re-mints and retries once. If Yahoo is still throttling, the worst case is that `Ticker.info` comes back empty: `_describe` returns None, `meta_fetched_at` is not stamped, and the metadata is fetched again next run. The crumb is never written to disk.
- **A guard would mean writing to yfinance's private `YfData()._crumb`** from folio, a fragile seam for a self-healing, run-scoped fault. Candidates 1 and 4 above reduce how often the 429 happens in the first place and are the better fix.
