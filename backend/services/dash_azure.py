"""Reads a DASH PDF with Azure Document Intelligence and returns markdown.

The only network call in the PARSING path — routes/dash.py makes one of its
own when a broker supplies a link rather than a file. Everything
downstream (dash_parse, dash_apply) works on the markdown string, so it is
testable from a fixture with no credentials and no Azure bill.

WHY AZURE AND NOT GEMINI. The deleted services/dash_gemini.py recorded a
limitation it could not fix after two rounds of increasingly explicit
prompting: DASH prints "SMITH,JOHN A" with no space after the comma, and the
model reliably returned "SMITH, JOHN A". Its own conclusion — "a
vision-language model reconstructs layout whitespace rather than transcribing
it."

Azure transcribes. Run against a real 8-page report on 2026-08-09 it returned
"FRANCIS,MILLAT F" and "5 FESTIVAL DR, , NORTH YORK ON M2R3T9" — the missing
space and the empty comma-separated part both byte-exact, first try.

AND NO MODEL IS NEEDED ON TOP, which an earlier design assumed. The two
decisions thought to need judgement — which operator block is the subject,
which claims are theirs — turned out to be string matches on the licence
number, which the same report showed is printed on every block.
"""

import os
from functools import lru_cache

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import DocumentContentFormat
from azure.core.credentials import AzureKeyCredential


# How long to wait for one analysis. The real 8-page report comes back in a
# few seconds, so 90 is generous rather than tight — it is there to end a hang,
# not to hurry a slow day at Azure. A broker who has waited a minute and a half
# has already decided the feature is not working.
_ANALYSIS_TIMEOUT_SECONDS = 90


@lru_cache(maxsize=1)
def _client() -> DocumentIntelligenceClient:
    """Built on first use, not at import.

    Importing this module must never require credentials — the parser's tests
    import the package to reach dash_parse, and a module-level client would
    make every one of them need an Azure key to collect.
    """
    endpoint = os.environ.get("AZURE_DOC_INTELLIGENCE_ENDPOINT")
    key = os.environ.get("AZURE_DOC_INTELLIGENCE_KEY")
    if not endpoint or not key:
        raise RuntimeError(
            "AZURE_DOC_INTELLIGENCE_ENDPOINT / _KEY are not configured — "
            "a DASH report cannot be read without them"
        )
    return DocumentIntelligenceClient(endpoint=endpoint, credential=AzureKeyCredential(key))


def read_pdf(pdf_bytes: bytes) -> str:
    """The report as markdown.

    prebuilt-layout, not prebuilt-document: layout returns headings, reading
    order and real tables, which is what the parser keys on. The generic
    key/value pairs prebuilt-document adds would cost more and answer nothing
    the markdown does not.

    MARKDOWN rather than the default JSON for the same reason — DASH's
    policies and claims are printed as tables, and markdown keeps them as
    tables instead of a flat list of cells whose rows have to be rebuilt from
    coordinates.

    BOUNDED. Without a timeout, poller.result() waits forever: an analysis
    that never finishes holds a threadpool worker and leaves the broker
    watching a spinner with nothing to do about it. Moving this off the event
    loop stopped one report freezing the whole API — it did not stop one
    report hanging its own request.

    Raises TimeoutError past the deadline, which services/dash.py turns into
    "that report could not be read" and the broker types the fields instead.
    That is the right outcome: this feature is optional, and waiting is worse
    than doing it by hand.
    """
    poller = _client().begin_analyze_document(
        "prebuilt-layout",
        body=pdf_bytes,
        content_type="application/pdf",
        output_content_format=DocumentContentFormat.MARKDOWN,
    )
    return poller.result(timeout=_ANALYSIS_TIMEOUT_SECONDS).content
