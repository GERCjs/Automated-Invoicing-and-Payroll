import re

import pytest
from django.urls import reverse


@pytest.mark.django_db
def test_homepage_loads_in_browser(live_server, page):
    page.goto(f"{live_server.url}{reverse('home')}")

    expect_text = re.compile("automated invoicing|invoice|payroll", re.IGNORECASE)
    assert page.locator("body").get_by_text(expect_text).first.is_visible()
