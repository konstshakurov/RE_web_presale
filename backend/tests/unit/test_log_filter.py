import io
import json
import logging

from app.logging_setup import REDACTED, configure_logging, redact

PASSWORD = "FAKE_Sup3r-Pass-w0rd"
TOKEN = "FAKE_abcDEF0123456789token"
COOKIE = "FAKE_JSESSIONID=ZZZ999sessionvalue; atlassian.xsrf.token=XSRF777value"
ENC = "S0VZLUtFWS1LRVktS0VZLUtFWS1LRVktS0VZLUtFWS0="


def run(fn, secrets=()):
    buf = io.StringIO()
    configure_logging(secrets, stream=buf)
    fn(logging.getLogger("t." + fn.__name__))
    return buf.getvalue()


def test_text_and_args_redacted():
    def emit(log):
        log.warning("login failed password=%s token: %s", PASSWORD, TOKEN)
        log.info("headers Authorization: Bearer %s", TOKEN)
        log.info("Cookie: %s", COOKIE)
        log.info("Set-Cookie: %s; Path=/", COOKIE)
        log.info("headers=%s", {"Authorization": "Basic QWxhZGRpbjpvcGVu", "Cookie": COOKIE})
        log.info("db postgresql://app_rw:%s@db:5432/app_db", PASSWORD)

    out = run(emit)
    for secret in (PASSWORD, TOKEN, "ZZZ999", "XSRF777", "QWxhZGRpbjpvcGVu"):
        assert secret not in out
    assert REDACTED in out
    for line in out.splitlines():
        json.loads(line)  # JSON-логи


def test_extra_fields_redacted():
    def emit(log):
        log.info("req", extra={"password": PASSWORD, "headers": {"Cookie": COOKIE},
                               "note": f"token={TOKEN}", "user": "ivan"})

    out = run(emit)
    assert PASSWORD not in out and "ZZZ999" not in out and TOKEN not in out
    assert json.loads(out)["user"] == "ivan"


def test_known_secret_values_redacted_anywhere():
    def emit(log):
        log.error("boom %s", ENC)

    assert ENC not in run(emit, [ENC])


def test_exception_text_redacted():
    def emit(log):
        try:
            raise RuntimeError(f"bad password={PASSWORD}")
        except RuntimeError:
            log.exception("failed")

    out = run(emit)
    assert PASSWORD not in out and "exc" in json.loads(out)


def test_plain_text_untouched():
    assert redact("sync done: 12 needs") == "sync done: 12 needs"
