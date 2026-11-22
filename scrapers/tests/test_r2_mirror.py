import hashlib
import json
from pathlib import Path

import httpx
import pytest

from toronto_bids import r2_mirror
from toronto_bids.r2_mirror import mirror_database

ACCOUNT = "a" * 32
TOKEN_ID = "b" * 32
TOKEN = "fixture-token-never-log"


class Storage:
    def __init__(self, *, wrong_size=False, fail=False, wrong_hash=False, change_source=False):
        self.wrong_size, self.fail = wrong_size, fail
        self.wrong_hash, self.change_source = wrong_hash, change_source
        self.calls = []
        self.closed = False

    def upload_file(self, path, bucket, key, *, ExtraArgs, Config):
        self.calls.append((path, bucket, key, ExtraArgs, Config))
        if self.fail:
            raise RuntimeError("upload failed")
        if self.change_source:
            Path(path).write_bytes(Path(path).read_bytes() + b"changed")

    def head_object(self, **kwargs):
        path, _, _, extra, _ = self.calls[-1]
        return dict(
            ContentLength=Path(path).stat().st_size + int(self.wrong_size),
            Metadata={"sha256": "different"} if self.wrong_hash else extra["Metadata"],
        )

    def close(self):
        self.closed = True


def _make_sqlite(tmp_path):
    path = tmp_path / "bids.sqlite"
    path.write_bytes(b"SQLite format 3\0" + b"published bytes")
    return path


def run(tmp_path, *, user_token=False, wrong_size=False, fail=False, wrong_hash=False, change_source=False):
    path = _make_sqlite(tmp_path)
    calls, factories = [], []

    def handle(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer " + TOKEN
        if user_token and "/accounts/" in request.url.path:
            return httpx.Response(401, json={"success": False})
        return httpx.Response(200, json={"success": True, "result": {"id": TOKEN_ID, "status": "active"}})

    storage = Storage(wrong_size=wrong_size, fail=fail, wrong_hash=wrong_hash, change_source=change_source)

    def factory(service, **kwargs):
        factories.append((service, kwargs))
        return storage

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        result = mirror_database(
            path, bucket="toronto-bids-data", account_id=ACCOUNT,
            api_token=TOKEN, http=http, s3_factory=factory,
        )
    return result, storage, calls, factories, path


def test_account_token_upload_uses_multipart_and_checks_hash_and_size(tmp_path):
    result, storage, calls, factories, path = run(tmp_path)
    assert len(calls) == 1 and calls[0].url.path == f"/client/v4/accounts/{ACCOUNT}/tokens/verify"
    assert factories[0][1]["aws_access_key_id"] == TOKEN_ID
    assert factories[0][1]["aws_secret_access_key"] == hashlib.sha256(TOKEN.encode()).hexdigest()
    _, bucket, key, extra, config = storage.calls[0]
    assert bucket == "toronto-bids-data" and key == "bids.sqlite"
    assert config.multipart_threshold < 300 * 1024**2
    assert 5 * 1024**2 <= config.multipart_chunksize < 300 * 1024**2
    assert config.max_concurrency <= 2
    assert extra["ContentType"] == "application/x-sqlite3"
    assert result["bytes"] == path.stat().st_size
    assert result["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert TOKEN not in str(result) and TOKEN_ID not in str(result)
    assert storage.closed is True


def test_existing_user_token_falls_back_to_user_verification(tmp_path):
    _, _, calls, _, _ = run(tmp_path, user_token=True)
    assert calls[-1].url.path == "/client/v4/user/tokens/verify"


def test_remote_size_mismatch_refuses_success(tmp_path):
    with pytest.raises(ValueError, match="size"):
        run(tmp_path, wrong_size=True)


def test_upload_failure_is_not_reported_as_success_and_closes_storage(tmp_path):
    path = _make_sqlite(tmp_path)
    storage = Storage(fail=True)
    ok = lambda req: httpx.Response(200, json={"success": True, "result": {"id": TOKEN_ID, "status": "active"}})
    with httpx.Client(transport=httpx.MockTransport(ok)) as http:
        with pytest.raises(RuntimeError, match="upload failed"):
            mirror_database(
                path, bucket="toronto-bids-data", account_id=ACCOUNT,
                api_token=TOKEN, http=http, s3_factory=lambda *_a, **_k: storage,
            )
    assert storage.closed is True


def test_remote_fingerprint_mismatch_refuses_success(tmp_path):
    with pytest.raises(ValueError, match="fingerprint"):
        run(tmp_path, wrong_hash=True)


def test_source_changed_during_transfer_refuses_success(tmp_path):
    with pytest.raises(ValueError, match="changed during upload"):
        run(tmp_path, change_source=True)


def test_inactive_token_never_starts_upload(tmp_path):
    path = tmp_path / "bids.sqlite"
    path.write_bytes(b"SQLite format 3\0")

    def inactive(request):
        return httpx.Response(200, json={"success": True, "result": {"id": TOKEN_ID, "status": "disabled"}})

    def forbidden(*args, **kwargs):
        pytest.fail("Inactive token must not create S3 client")

    with httpx.Client(transport=httpx.MockTransport(inactive)) as http:
        with pytest.raises(ValueError, match="active"):
            mirror_database(
                path, bucket="toronto-bids-data", account_id=ACCOUNT,
                api_token=TOKEN, http=http, s3_factory=forbidden,
            )


def test_token_verification_http_500_and_both_scopes_rejected(tmp_path):
    path = _make_sqlite(tmp_path)
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(500))) as http:
        with pytest.raises(ValueError, match="HTTP 500"):
            mirror_database(path, bucket="toronto-bids-data", account_id=ACCOUNT,
                            api_token=TOKEN, http=http)

    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(403))) as http:
        with pytest.raises(ValueError, match="account and user scopes"):
            mirror_database(path, bucket="toronto-bids-data", account_id=ACCOUNT,
                            api_token=TOKEN, http=http)


@pytest.mark.parametrize("account,bucket", [("not-an-account", "valid-bucket"), (ACCOUNT, "../other")])
def test_invalid_destination_is_rejected_before_authentication(tmp_path, account, bucket):
    path = tmp_path / "bids.sqlite"
    path.write_bytes(b"SQLite format 3\0")
    with pytest.raises(ValueError):
        mirror_database(path, bucket=bucket, account_id=account, api_token=TOKEN)


def test_missing_api_token_and_non_sqlite_file_are_rejected(tmp_path):
    sqlite_file = _make_sqlite(tmp_path)
    with pytest.raises(ValueError, match="CLOUDFLARE_API_TOKEN"):
        mirror_database(sqlite_file, bucket="toronto-bids-data", account_id=ACCOUNT, api_token="")

    not_sqlite = tmp_path / "not.sqlite"
    not_sqlite.write_bytes(b"plain text file")
    with pytest.raises(ValueError, match="not a SQLite database"):
        mirror_database(not_sqlite, bucket="toronto-bids-data", account_id=ACCOUNT, api_token=TOKEN)


def test_main_cli_success_and_error(tmp_path, monkeypatch, capsys):
    path = _make_sqlite(tmp_path)
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", ACCOUNT)
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", TOKEN)
    monkeypatch.setattr(
        r2_mirror, "mirror_database",
        lambda *a, **k: {"bucket": "b", "key": "bids.sqlite", "bytes": 31, "sha256": "abc", "verified": True},
    )
    assert r2_mirror.main(["--file", str(path), "--bucket", "b"]) == 0
    assert json.loads(capsys.readouterr().out)["verified"] is True

    monkeypatch.setattr(
        r2_mirror, "mirror_database",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("bad token")),
    )
    assert r2_mirror.main(["--file", str(path)]) == 1
    assert "R2 mirror failed: bad token" in capsys.readouterr().err
