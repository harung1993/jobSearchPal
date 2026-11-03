"""Job mail is read from All Mail, Spam and Trash, read-only, without duplicates."""
from autoapply import alerts

MSG = b"Message-ID: <a@x>\r\nFrom: LinkedIn <jobalerts-noreply@linkedin.com>\r\nSubject: New jobs\r\nDate: Tue, 3 Nov 2026 08:00:00 -0500\r\n\r\nAnalytics Engineer at Hooli"


class FakeIMAP:
    selected = []

    def __init__(self, host): pass
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def login(self, u, p): pass

    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\All \\HasNoChildren) "/" "[Gmail]/All Mail"',
                      b'(\\HasNoChildren \\Junk) "/" "[Gmail]/Spam"', b'(\\HasNoChildren \\Trash) "/" "[Gmail]/Bin"']

    def select(self, box, readonly=False):
        assert readonly
        FakeIMAP.selected.append(box)
        return "OK", [b"1"]

    def search(self, *a): return "OK", [b"1"]
    def fetch(self, i, what):
        assert "PEEK" in what
        return "OK", [(b"1", MSG)]


def test_reads_spam_and_trash_once_each(monkeypatch):
    monkeypatch.setenv("GMAIL_USER", "me@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "x")
    monkeypatch.setattr(alerts.imaplib, "IMAP4_SSL", FakeIMAP)
    FakeIMAP.selected = []
    got = alerts.fetch(days=1)
    assert FakeIMAP.selected == ['"[Gmail]/All Mail"', '"[Gmail]/Spam"', '"[Gmail]/Bin"']
    assert len(got) == 1 and "Hooli" in got[0]["text"]  # same Message-ID in several folders: once


def test_cli_reads_dotenv(tmp_path, monkeypatch):
    from autoapply import config
    (tmp_path / ".env").write_text('# comment\nexport GMAIL_USER="me@example.com"\nGMAIL_APP_PASSWORD=abcd efgh\nBIND=already\n')
    (tmp_path / "config.yaml").write_text("data_dir: data\n")
    for k in ("GMAIL_USER", "GMAIL_APP_PASSWORD"):  # set then unset, so the test's values are undone afterwards
        monkeypatch.setenv(k, "x")
        monkeypatch.delenv(k)
    monkeypatch.setenv("BIND", "set-by-shell")
    config.load(str(tmp_path / "config.yaml"))
    import os
    assert os.environ["GMAIL_USER"] == "me@example.com" and os.environ["GMAIL_APP_PASSWORD"] == "abcd efgh"
    assert os.environ["BIND"] == "set-by-shell"
