from email.message import EmailMessage

from autoapply.alerts import html_to_text, message_text


def test_alert_html_keeps_job_links():
    msg = EmailMessage()
    msg["From"] = "jobalerts-noreply@linkedin.com"
    msg.set_content("plain fallback")
    msg.add_alternative('<table><tr><td><a href="https://www.linkedin.com/jobs/view/123">Senior Analytics Engineer</a>'
                        '<br>Acme &middot; Remote</td></tr></table>', subtype="html")
    text = message_text(msg)
    assert "Senior Analytics Engineer (https://www.linkedin.com/jobs/view/123)" in text
    assert "Acme · Remote" in text


def test_html_to_text_drops_styles():
    assert html_to_text("<style>p{}</style><p>a</p><p>b</p>") == "a\nb"
