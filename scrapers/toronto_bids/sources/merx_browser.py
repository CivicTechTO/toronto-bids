"""Ordinary anonymous browser requests after an incomplete MERX HTTP response.

No stealth settings, account access, challenge interaction or alternate endpoints.
The caller controls pacing, request budgets, retained bytes and server deadlines.
"""
from urllib.parse import urlsplit
import httpx

from toronto_bids.sources.merx import USER_AGENT


class BrowserReader:
    def __init__(self):
        from playwright.sync_api import sync_playwright
        self.playwright = sync_playwright().start()
        try:
            self.browser = self.playwright.chromium.launch(headless=True)
            self.context = self.browser.new_context(user_agent=USER_AGENT)
            self.page = self.context.new_page()
            self.before_request = lambda: None
            self.navigations = 0
            self.route_error = None
            def route(request_route):
                request = request_route.request
                host = urlsplit(request.url).netloc
                ordinary_asset = host == 'www.merx.com' or host.endswith('.token.awswaf.com')
                if (request.resource_type in ('image', 'media', 'font') or not ordinary_asset):
                    request_route.abort()
                else:
                    if host == 'www.merx.com' and request.resource_type in ('document', 'xhr', 'fetch'):
                        if request.is_navigation_request():
                            self.navigations += 1
                            if self.navigations > 2:
                                request_route.abort()
                                return
                        try:
                            self.before_request()
                        except Exception as exc:
                            self.route_error = exc
                            request_route.abort()
                            return
                    request_route.continue_()
            self.page.route('**/*', route)
        except BaseException:
            self.playwright.stop()
            raise

    def get(self, url, *, prepare, before_request, record):
        self.before_request = before_request
        def navigate(target):
            self.navigations = 0
            documents = []
            def observed(response):
                if response.request.is_navigation_request() and response.frame == self.page.main_frame:
                    documents.append(response)
            self.page.on('response', observed)
            self.route_error = None
            try:
                response = self.page.goto(target, wait_until='domcontentloaded', timeout=60000)
                if response is None:
                    raise RuntimeError('MERX browser navigation returned no response')
                result = httpx.Response(response.status, content=response.body(),
                    headers=response.all_headers(), request=httpx.Request('GET', response.url))
                record(result)
                if result.status_code == 202 and result.headers.get('x-amzn-waf-action') == 'challenge':
                    # Let the site's normal JavaScript render the public page. No
                    # token API calls, injection, puzzle interaction or stealth.
                    try:
                        self.page.wait_for_function("document.title.startsWith('MERX : ')", timeout=30000)
                    except Exception as exc:
                        raise RuntimeError('MERX browser HTTP 202: public page did not load after normal rendering') from exc
                    response = documents[-1]
                    result = httpx.Response(response.status, content=response.body(),
                        headers=response.all_headers(), request=httpx.Request('GET', response.url))
                    record(result)
                if result.status_code != 200 or not result.content:
                    raise RuntimeError(f'MERX browser HTTP {result.status_code}: no complete public document')
                return result
            except Exception:
                if self.route_error is not None:
                    raise self.route_error
                raise
            finally:
                self.page.remove_listener('response', observed)
        if '/abstract/' not in urlsplit(url).path:
            return navigate(url)
        # The fragment is an ordinary public UI XHR, using the anonymous browser
        # context of the matching detail page. Never guess a bootstrap page.
        if not prepare:
            raise RuntimeError('MERX fragment browser fallback requires its held public detail URL')
        navigate(prepare)
        before_request()
        response = self.context.request.get(url, headers={'X-Requested-With': 'XMLHttpRequest'},
                                            max_redirects=0, timeout=60000)
        result = httpx.Response(response.status, content=response.body(),
            headers=response.all_headers(), request=httpx.Request('GET', response.url))
        record(result)
        return result

    def cookies(self):
        return self.context.cookies()  # anonymous state only; never logged or persisted

    def close(self):
        try:
            self.context.close()
            self.browser.close()
        finally:
            self.playwright.stop()
