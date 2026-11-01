from datetime import date, timedelta

import requests

from stock_etf.etf_disclosures import _download_candidates


def _recent_date(days_ago=10):
    return (date.today() - timedelta(days=days_ago)).strftime("%d %b %Y")


def test_motilal_embedded_portfolio_url_inherits_scheme_page_reporting_date():
    reporting_date = _recent_date()
    html = f"""
    <html><body>
      <h1>Motilal Oswal Nasdaq 100 ETF</h1>
      <div>Date As On: {reporting_date}</div>
      <script>
        portfolioUrl: /content/dam/motilal-mf/sheets/fund-csvs/Month_End_Portfolio/YO03.xlsx
      </script>
    </body></html>
    """

    class Response:
        text = html

        def raise_for_status(self):
            return None

    class Session:
        def get(self, url, timeout):
            return Response()

    candidates, error = _download_candidates(
        Session(),
        "motilal",
        ("Motilal Oswal Nasdaq 100 ETF",),
        page_cache={},
        max_age_days=40,
    )

    assert error is None
    assert len(candidates) == 1
    assert candidates[0][0].endswith("/YO03.xlsx")
    assert reporting_date in candidates[0][1]


def test_icici_legacy_official_page_is_used_after_current_page_http_error():
    reporting_date = _recent_date()
    html = f"""
    <a href="/downloads/ICICI-Nifty-50-ETF-Portfolio.xlsx">
      ICICI Prudential Nifty 50 ETF monthly portfolio as on {reporting_date}
    </a>
    """

    class Response:
        text = html

        def raise_for_status(self):
            return None

    class Session:
        def get(self, url, timeout):
            if "media-center/downloads" in url:
                response = requests.Response()
                response.status_code = 403
                raise requests.HTTPError("403", response=response)
            return Response()

    candidates, error = _download_candidates(
        Session(),
        "icici",
        ("ICICI Prudential Nifty 50 ETF",),
        page_cache={},
        max_age_days=40,
    )

    assert error is None
    assert len(candidates) == 1
    assert "ICICI-Nifty-50-ETF-Portfolio.xlsx" in candidates[0][0]



def test_mirae_monthly_portfolio_adapter_uses_official_portfolio_page():
    from stock_etf.etf_disclosures import SOURCE_PAGES

    reporting_date = _recent_date()
    html = f"""
    <a href="/downloads/MAHKTECH-portfolio.xlsx">
      Monthly portfolio for Mirae Asset Hang Seng TECH ETF as on {reporting_date}
    </a>
    """

    class Response:
        text = html

        def raise_for_status(self):
            return None

    class Session:
        def get(self, url, timeout):
            assert url == "https://www.miraeassetmf.co.in/downloads/portfolio"
            return Response()

    assert SOURCE_PAGES["mirae"] == "https://www.miraeassetmf.co.in/downloads/portfolio"
    candidates, error = _download_candidates(
        Session(),
        "mirae",
        ("Mirae Asset Hang Seng TECH ETF",),
        page_cache={},
        max_age_days=40,
    )

    assert error is None
    assert len(candidates) == 1
    assert "MAHKTECH-portfolio.xlsx" in candidates[0][0]
