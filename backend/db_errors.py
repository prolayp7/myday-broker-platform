import logging

from fastapi import HTTPException

logger = logging.getLogger("myday.db")

# Postgres error codes we translate into meaningful HTTP responses.
FOREIGN_KEY_VIOLATION = "23503"
UNIQUE_VIOLATION = "23505"
# P0001 = raise_exception from a PL/pgSQL trigger. We use it for the
# broker-group cap trigger (migration 0014), which is the DB-level backstop
# against concurrent onboards/moves overshooting the 10-broker cap.
RAISE_EXCEPTION = "P0001"


def db_error(
    err: Exception,
    *,
    on_foreign_key: HTTPException | None = None,
    on_unique: HTTPException | None = None,
    on_group_full: HTTPException | None = None,
) -> HTTPException:
    """Translate a Supabase/Postgres exception into an HTTPException.

    Pass the route-specific response for whichever constraint(s) that route
    can actually hit (e.g. a 409 "still in use" for a foreign-key violation).
    Anything unmapped is logged and returned as a generic 500 so we never leak
    internal details to the client but still keep a server-side trail to debug
    from.

    WHAT IS LOGGED, AND WHAT IS DELIBERATELY NOT. Postgres splits a failure
    into a `message` naming the CONSTRAINT and a `details` carrying the DATA
    that broke it:

        message  violates unique constraint "broker_email_key"
        details  Key (email)=(someone@example.com) already exists.

    and on a check or not-null violation `details` is worse still — it is
    "Failing row contains (...)", the entire row, every column. This used to
    log str(err), which is the whole APIError dict including both.

    So only `message` and `code` are logged. That is what identifies the bug;
    the values are what identify a person. The two constraints that would
    expose real people here are broker.email and lead_phone.phone_e164 — a
    customer's phone number.
    """
    # .message / .code come from postgrest's APIError. Anything else (a plain
    # exception from a non-Supabase path) falls back to str(err) — which is
    # that exception's own text, not a Postgres error carrying row data.
    message = getattr(err, "message", None) or str(err)
    code = getattr(err, "code", None)

    def _is(sqlstate: str) -> bool:
        """Match on the error CODE when we have one, falling back to the old
        substring test otherwise.

        The fallback is not decoration: these branches used to match against
        str(err) — the full dict, where the code appeared as "'code': '23505'".
        Reading .code is exact, but a caller passing something that is not an
        APIError would silently stop routing without this, turning a mapped
        409 into a generic 500."""
        return code == sqlstate if code is not None else sqlstate in str(err)

    if on_foreign_key is not None and _is(FOREIGN_KEY_VIOLATION):
        return on_foreign_key
    if on_unique is not None and _is(UNIQUE_VIOLATION):
        return on_unique
    if on_group_full is not None and _is(RAISE_EXCEPTION):
        return on_group_full

    # logger.error, NOT logger.exception. exception() appends the traceback,
    # whose last line is the exception's own repr — the full APIError dict,
    # `details` and all. Sanitising the message while exception() reprints the
    # raw error underneath it would have been a fix that changed nothing.
    #
    # The traceback is the real loss here, and it is worth it: the constraint
    # name plus the code says WHAT failed, and the 500 it returns is raised at
    # the call site, so FastAPI still logs where the request died.
    logger.error("Unhandled database error [%s]: %s", code, message)
    return HTTPException(status_code=500, detail="Something went wrong")
