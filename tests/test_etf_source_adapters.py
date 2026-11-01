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


def test_icici_passive_factsheet_page_discovers_pdf_for_scheme():
    from stock_etf.etf_disclosures import SOURCE_PAGES

    reporting_month_date = date.today() - timedelta(days=10)
    reporting_month = reporting_month_date.strftime("%B %Y")
    html = f"""
    <h1>ICICI Prudential Nifty 50 ETF</h1>
    <h2>ICICI Prudential Nifty Next 50 ETF</h2>
    <a href="/passive/pdf/passive-fund-factsheet-{reporting_month_date.strftime('%B').lower()}-{reporting_month_date.year}.pdf">
      Passive Fund Factsheet {reporting_month}
    </a>
    """

    class Response:
        text = html

        def raise_for_status(self):
            return None

    class Session:
        def get(self, url, timeout):
            assert url == "https://digitalfactsheet.icicipruamc.com/passive/"
            return Response()

    assert SOURCE_PAGES["icici"] == "https://digitalfactsheet.icicipruamc.com/passive/"
    candidates, error = _download_candidates(
        Session(),
        "icici",
        ("ICICI Prudential Nifty 50 ETF",),
        page_cache={},
        max_age_days=40,
    )

    assert error is None
    assert len(candidates) == 1
    assert candidates[0][0].endswith(".pdf")
    assert reporting_month in candidates[0][1]



def test_mirae_scheme_page_discovers_full_portfolio_download():
    from stock_etf.etf_disclosures import SOURCE_PAGES

    html = """
    <h1>MIRAE ASSET HANG SENG TECH ETF</h1>
    <a href="/wp-content/uploads/2026/10/MAHKTECH-Full-Portfolio.pdf">
      Full Portfolio
    </a>
    """

    class Response:
        text = html

        def raise_for_status(self):
            return None

    class Session:
        def get(self, url, timeout):
            assert url == (
                "https://miraeassetetf.co.in/our-etfs/fund?"
                "nseSymbol=MAHKTECH&schemeCode=HTRG"
            )
            return Response()

    assert SOURCE_PAGES["mirae"] == (
        "https://miraeassetetf.co.in/our-etfs/fund?"
        "nseSymbol=MAHKTECH&schemeCode=HTRG"
    )
    candidates, error = _download_candidates(
        Session(),
        "mirae",
        ("Mirae Asset Hang Seng TECH ETF",),
        page_cache={},
        max_age_days=40,
    )

    assert error is None
    assert len(candidates) == 1
    assert candidates[0][0].endswith("MAHKTECH-Full-Portfolio.pdf")
    assert "Mirae Asset Hang Seng TECH ETF" in candidates[0][1]



def test_pdf_disclosure_table_parser_extracts_scheme_holdings(monkeypatch):
    import sys
    from types import SimpleNamespace

    from stock_etf.etf_disclosures import _extract_rows, _read_workbook

    class Page:
        def extract_text(self):
            return "ICICI Prudential Nifty 50 ETF Portfolio as on 30 September 2026"

        def extract_tables(self):
            return [[
                ["Company/Issuer", "% to NAV", "Industry"],
                ["Reliance Industries Limited", "8.25%", "Energy"],
                ["HDFC Bank Limited", "7.50%", "Banks"],
            ]]

    class PDF:
        pages = [Page()]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setitem(sys.modules, "pdfplumber", SimpleNamespace(open=lambda _payload: PDF()))
    frames = _read_workbook(
        b"fake-pdf",
        "https://digitalfactsheet.icicipruamc.com/passive/pdf/passive-fund-factsheet-september-2026.pdf",
    )
    rows, error = _extract_rows(
        frames,
        ("ICICI Prudential Nifty 50 ETF",),
    )

    assert error is None
    assert len(rows) == 2
    assert rows[0]["weight_pct"] == 8.25
    assert rows[0]["sector"] == "Energy"
