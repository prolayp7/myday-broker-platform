import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from supabase import create_client, Client
from supabase.lib.client_options import SyncClientOptions

load_dotenv(Path(__file__).resolve().parent / ".env")

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

# HTTP/1.1, NOT HTTP/2 — and this line is load-bearing.
#
# This one client is shared by every request thread (FastAPI runs sync
# endpoints in a threadpool) AND by the four background drains, each in its
# own worker thread. postgrest-py builds its httpx client with http2=True by
# default, and HTTP/2 multiplexes every request over ONE connection: two
# threads talking at once interleave on the same socket, one of them reads
# mid-frame, and httpx surfaces it as
#
#     httpx.ReadError: [Errno 11] Resource temporarily unavailable   (Linux)
#     httpx.ReadError: [WinError 10035] ... could not be completed   (Windows)
#
# which the request sees as a 500. It bit a broker in production on
# 2026-09-21 00:48 UTC — GET /leads/mine, the list reload right after a stage
# change — and reproduced locally the moment a browser fired its five
# per-lead requests in parallel. Intermittent by nature, so it "went away"
# on refresh, which is what made it look like a frontend bug.
#
# With HTTP/1.1 the pool hands each concurrent request its own connection,
# which is the mode httpx's Client is documented thread-safe in. The timeout
# matches postgrest's own default so nothing else changes.
_http = httpx.Client(http2=False, timeout=httpx.Timeout(20.0), follow_redirects=True)

supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_SERVICE_ROLE_KEY,
    options=SyncClientOptions(httpx_client=_http),
)
