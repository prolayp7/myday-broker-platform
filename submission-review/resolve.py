"""Manual review commands. Every action records who did it and when.

  python resolve.py list
  python resolve.py broker <email> [name]
  python resolve.py assign <document-id> <SR-1001 | new> --by <you>
  python resolve.py set <SR-1001> <detail> <value> --by <you>
  python resolve.py retry <document-id> --by <you>

`set` takes a detail as the result email names it, such as q_fraud or
vehicle.1.car_pool. The worker picks up the change on its next pass.
"""

import argparse

import settings
import store
import worker


def _close(by: str, action: str, **match) -> None:
    for item in store.select("manual_review", status="open", **match):
        store.update("manual_review", item["id"], status="resolved", action=action,
                     resolved_by=by, resolved_at=store.now())


def list_open(_) -> None:
    for item in store.select("manual_review", status="open"):
        print(item["id"], "| document", item["document_id"], "| file", item["submission_id"],
              "|", item["reason"])


def add_broker(args) -> None:
    store.insert("broker", {"email": args.email.lower(), "name": args.name, "approved": True})
    print("added")


def assign(args) -> None:
    document = store.select("document", "*, email(from_address, subject)", id=args.document)[0]
    broker = store.select("broker", email=document["email"]["from_address"])[0]
    if args.file == "new":
        if document["doc_type"] != "application" or document["read_status"] != "ok":
            raise SystemExit("Only an application that read correctly can start a file")
        submission = store.insert("submission", {"broker_id": broker["id"]})
    else:
        found = store.select("submission", reference=args.file.upper(), broker_id=broker["id"])
        if not found:
            raise SystemExit("That file does not belong to this document's broker")
        submission = found[0]
    if document["read_status"] != "ok":
        raise SystemExit("This document did not read correctly: use retry first")
    store.update("document", document["id"], submission_id=None)
    worker._attach(document, submission["id"])
    _close(args.by, "assign", document_id=document["id"])
    print("assigned to", submission["reference"])


def _detail_row(application_id: str, detail: str) -> tuple[str, dict, str]:
    parts = detail.split(".")
    if len(parts) == 1:
        return "application", {"id": application_id}, detail
    if len(parts) == 3 and parts[0] == "driver":
        return "app_driver", {"application_id": application_id, "driver_no": parts[1]}, parts[2]
    if len(parts) == 3 and parts[0] == "vehicle":
        return "app_vehicle", {"application_id": application_id, "auto_no": parts[1]}, parts[2]
    raise SystemExit(f"Unknown detail: {detail}")


def set_value(args) -> None:
    submission = store.select("submission", reference=args.file.upper())[0]
    document = next(d for d in store.active_documents(submission["id"]) if d["doc_type"] == "application")
    application_id = store.select("application", "id", document_id=document["id"])[0]["id"]
    table, keys, column = _detail_row(application_id, args.detail)
    query = store._db().table(table).update({column: args.value})
    for key, value in keys.items():
        query = query.eq(key, value)
    query.execute()
    raw = document["raw"]
    raw["_unclear"] = [item for item in raw.get("_unclear", []) if item != args.detail]
    raw.setdefault("_corrections", []).append(
        {"detail": args.detail, "value": args.value, "by": args.by, "at": store.now()}
    )
    store.update("document", document["id"], raw=raw)
    # The PDF has not changed, but the old result no longer holds.
    store.mark_outdated(submission["id"])
    print("saved; a new review runs on the next pass")


def retry(args) -> None:
    store.update("document", args.document, raw=None, doc_type=None, read_status="pending",
                 read_error=None, match_status="pending", process_status="stored", attempts=0,
                 last_error=None, submission_id=None)
    _close(args.by, "retry", document_id=args.document)
    print("will be read again on the next pass")


def main() -> None:
    settings.load()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    commands = parser.add_subparsers(required=True)
    commands.add_parser("list").set_defaults(run=list_open)

    broker = commands.add_parser("broker")
    broker.add_argument("email")
    broker.add_argument("name", nargs="?")
    broker.set_defaults(run=add_broker)

    for name, run, arguments in (
        ("assign", assign, ("document", "file")),
        ("set", set_value, ("file", "detail", "value")),
        ("retry", retry, ("document",)),
    ):
        command = commands.add_parser(name)
        for argument in arguments:
            command.add_argument(argument)
        command.add_argument("--by", required=True)
        command.set_defaults(run=run)

    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
