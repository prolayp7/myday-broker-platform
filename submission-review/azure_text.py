"""Turns a PDF into text with Azure. Copied from MyDay's dash_azure, not shared."""

import os
from functools import lru_cache

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.ai.documentintelligence.models import DocumentContentFormat
from azure.core.credentials import AzureKeyCredential

# Ends a hang rather than hurrying a slow day at Azure.
_ANALYSIS_TIMEOUT_SECONDS = 90


@lru_cache(maxsize=1)
def _client() -> DocumentIntelligenceClient:
    return DocumentIntelligenceClient(
        endpoint=os.environ["AZURE_DOC_INTELLIGENCE_ENDPOINT"],
        credential=AzureKeyCredential(os.environ["AZURE_DOC_INTELLIGENCE_KEY"]),
    )


def read_pdf(pdf_bytes: bytes) -> str:
    """The document as markdown, which keeps its tables as tables."""
    poller = _client().begin_analyze_document(
        "prebuilt-layout",
        body=pdf_bytes,
        content_type="application/pdf",
        output_content_format=DocumentContentFormat.MARKDOWN,
    )
    return poller.result(timeout=_ANALYSIS_TIMEOUT_SECONDS).content
