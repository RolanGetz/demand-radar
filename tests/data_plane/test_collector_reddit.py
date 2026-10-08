"""Reddit collector — OAuth token minting and listing normalisation, HTTP mocked."""

from datetime import datetime, timezone

import httpx
import pytest
import respx

from demand_radar.data_plane.collectors import RedditCollector

_SEARCH = "https://oauth.reddit.com/search"
_TOKEN = "https://www.reddit.com/api/v1/access_token"


def _listing(*children, after=None):
    return {"data": {"after": after, "children": [{"data": child} for child in children]}}


@respx.mock
def test_parses_a_listing_into_signals():
    respx.get(_SEARCH).mock(
        return_value=httpx.Response(
            200,
            json=_listing(
                {
                    "name": "t3_abc",
                    "id": "abc",
                    "title": "Freezing up in English calls",
                    "selftext": "I understand the client but cannot answer fast enough.",
                    "author": "carol",
                    "score": 17,
                    "created_utc": 1_700_000_000,
                    "permalink": "/r/sales/comments/abc/",
                    "subreddit": "sales",
                }
            ),
        )
    )
    page = RedditCollector(access_token="token").collect("english calls", limit=5)

    signal = page.signals[0]
    assert signal.source == "reddit"
    assert signal.source_id == "t3_abc"
    assert signal.author == "carol"
    assert signal.score == 17
    assert signal.url == "https://www.reddit.com/r/sales/comments/abc/"
    assert signal.community == "r/sales"
    assert signal.created_at == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)
    assert "cannot answer fast enough" in signal.text


@respx.mock
def test_existing_token_is_used_without_minting_a_new_one():
    token_route = respx.post(_TOKEN)
    search_route = respx.get(_SEARCH).mock(return_value=httpx.Response(200, json=_listing()))

    RedditCollector(access_token="supplied").collect("acme")

    assert not token_route.called
    assert search_route.calls.last.request.headers["Authorization"] == "Bearer supplied"


@respx.mock
def test_app_credentials_mint_a_token_once_across_pages():
    token_route = respx.post(_TOKEN).mock(
        return_value=httpx.Response(200, json={"access_token": "minted"})
    )
    search_route = respx.get(_SEARCH).mock(return_value=httpx.Response(200, json=_listing()))

    collector = RedditCollector(client_id="id", client_secret="secret")
    collector.collect("acme")
    collector.collect("acme", cursor="t3_next")

    assert token_route.call_count == 1
    assert search_route.calls.last.request.headers["Authorization"] == "Bearer minted"


@respx.mock
def test_missing_credentials_raise_a_actionable_error():
    with pytest.raises(RuntimeError, match="REDDIT_CLIENT_ID"):
        RedditCollector().collect("acme")


@respx.mock
def test_token_response_without_a_token_is_rejected():
    respx.post(_TOKEN).mock(return_value=httpx.Response(200, json={"error": "unauthorized"}))
    with pytest.raises(RuntimeError, match="did not contain an access token"):
        RedditCollector(client_id="id", client_secret="secret").collect("acme")


@respx.mock
def test_cursor_is_forwarded_as_the_after_parameter():
    route = respx.get(_SEARCH).mock(return_value=httpx.Response(200, json=_listing()))
    RedditCollector(access_token="t").collect("acme", cursor="t3_prev")
    assert route.calls.last.request.url.params["after"] == "t3_prev"


@respx.mock
def test_next_cursor_comes_from_the_listing():
    respx.get(_SEARCH).mock(
        return_value=httpx.Response(200, json=_listing({"id": "x", "title": "acme"}, after="t3_more"))
    )
    assert RedditCollector(access_token="t").collect("acme").next_cursor == "t3_more"


@respx.mock
def test_limit_is_capped_at_the_api_maximum():
    route = respx.get(_SEARCH).mock(return_value=httpx.Response(200, json=_listing()))
    RedditCollector(access_token="t").collect("acme", limit=500)
    assert route.calls.last.request.url.params["limit"] == "100"


@respx.mock
def test_entry_without_permalink_falls_back_to_the_outbound_url():
    respx.get(_SEARCH).mock(
        return_value=httpx.Response(
            200,
            json=_listing({"id": "y", "title": "acme", "url": "https://elsewhere.test/post"}),
        )
    )
    page = RedditCollector(access_token="t").collect("acme")
    assert page.signals[0].url == "https://elsewhere.test/post"


# -- subreddit targeting ---------------------------------------------------
from demand_radar.data_plane.collectors.reddit import normalize_subreddits  # noqa: E402

SALES_SEARCH = "https://oauth.reddit.com/r/sales/search"
CONSULTING_SEARCH = "https://oauth.reddit.com/r/consulting/search"


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (["r/sales"], ["sales"]),
        (["/r/sales"], ["sales"]),
        (["/r/sales/"], ["sales"]),
        (["sales"], ["sales"]),
        (["R/Sales"], ["Sales"]),
        (["  r/sales  "], ["sales"]),
        (["r/sales", "sales"], ["sales"]),          # duplicate collapses
        (["r/sales", "r/SALES"], ["sales"]),        # case variant collapses
        (["r/sales", "r/consulting"], ["sales", "consulting"]),  # order kept
        (["", "  ", "r/"], []),                     # blanks dropped
    ],
)
def test_subreddit_names_are_normalised(given, expected):
    assert normalize_subreddits(given) == expected


@respx.mock
def test_a_subreddit_search_hits_the_subreddit_endpoint_and_restricts_it():
    """restrict_sr stops Reddit widening the query back out to the whole site."""
    route = respx.get(SALES_SEARCH).mock(return_value=httpx.Response(200, json=_listing()))
    site_wide = respx.get(_SEARCH)

    RedditCollector(access_token="t", subreddits=["r/sales"]).collect("pushback")

    assert route.called
    assert not site_wide.called
    assert route.calls.last.request.url.params["restrict_sr"] == "true"


@respx.mock
def test_without_subreddits_the_site_wide_endpoint_is_used():
    route = respx.get(_SEARCH).mock(return_value=httpx.Response(200, json=_listing()))
    RedditCollector(access_token="t").collect("pushback")
    assert route.called
    assert "restrict_sr" not in route.calls.last.request.url.params


@respx.mock
def test_each_subreddit_is_searched_separately():
    sales = respx.get(SALES_SEARCH).mock(
        return_value=httpx.Response(200, json=_listing({"id": "a", "title": "x", "subreddit": "sales"}))
    )
    consulting = respx.get(CONSULTING_SEARCH).mock(
        return_value=httpx.Response(
            200, json=_listing({"id": "b", "title": "y", "subreddit": "consulting"})
        )
    )

    page = RedditCollector(access_token="t", subreddits=["r/sales", "r/consulting"]).collect("q")

    assert sales.called and consulting.called
    assert {signal.community for signal in page.signals} == {"r/sales", "r/consulting"}


@respx.mock
def test_the_page_budget_is_split_across_subreddits():
    """One busy subreddit must not consume the whole limit and starve the others."""
    sales = respx.get(SALES_SEARCH).mock(return_value=httpx.Response(200, json=_listing()))
    respx.get(CONSULTING_SEARCH).mock(return_value=httpx.Response(200, json=_listing()))

    RedditCollector(access_token="t", subreddits=["sales", "consulting"]).collect("q", limit=50)
    assert sales.calls.last.request.url.params["limit"] == "25"


@respx.mock
def test_the_token_is_minted_once_for_all_subreddits():
    token = respx.post(_TOKEN).mock(return_value=httpx.Response(200, json={"access_token": "m"}))
    respx.get(SALES_SEARCH).mock(return_value=httpx.Response(200, json=_listing()))
    respx.get(CONSULTING_SEARCH).mock(return_value=httpx.Response(200, json=_listing()))

    RedditCollector(client_id="i", client_secret="s", subreddits=["sales", "consulting"]).collect("q")
    assert token.call_count == 1


@respx.mock
def test_per_subreddit_cursors_are_carried_in_one_cursor():
    respx.get(SALES_SEARCH).mock(
        return_value=httpx.Response(200, json=_listing({"id": "a", "title": "x"}, after="t3_s"))
    )
    respx.get(CONSULTING_SEARCH).mock(
        return_value=httpx.Response(200, json=_listing({"id": "b", "title": "y"}, after="t3_c"))
    )
    page = RedditCollector(access_token="t", subreddits=["sales", "consulting"]).collect("q")
    assert page.next_cursor == "sales:t3_s|consulting:t3_c"


@respx.mock
def test_a_composite_cursor_resumes_each_subreddit_at_its_own_depth():
    sales = respx.get(SALES_SEARCH).mock(return_value=httpx.Response(200, json=_listing()))
    consulting = respx.get(CONSULTING_SEARCH).mock(return_value=httpx.Response(200, json=_listing()))

    RedditCollector(access_token="t", subreddits=["sales", "consulting"]).collect(
        "q", cursor="sales:t3_s|consulting:t3_c"
    )
    assert sales.calls.last.request.url.params["after"] == "t3_s"
    assert consulting.calls.last.request.url.params["after"] == "t3_c"


@respx.mock
def test_an_exhausted_subreddit_drops_out_of_later_pages():
    """A subreddit with no `after` is finished; re-asking would restart it."""
    sales = respx.get(SALES_SEARCH).mock(return_value=httpx.Response(200, json=_listing()))
    consulting = respx.get(CONSULTING_SEARCH).mock(return_value=httpx.Response(200, json=_listing()))

    RedditCollector(access_token="t", subreddits=["sales", "consulting"]).collect(
        "q", cursor="sales:t3_s"
    )
    assert sales.called
    assert not consulting.called


@respx.mock
def test_the_cursor_is_none_once_every_subreddit_is_exhausted():
    respx.get(SALES_SEARCH).mock(return_value=httpx.Response(200, json=_listing(after=None)))
    page = RedditCollector(access_token="t", subreddits=["sales"]).collect("q")
    assert page.next_cursor is None


@respx.mock
def test_examined_counts_raw_items_across_subreddits():
    respx.get(SALES_SEARCH).mock(
        return_value=httpx.Response(200, json=_listing({"id": "a", "title": "x"}))
    )
    respx.get(CONSULTING_SEARCH).mock(
        return_value=httpx.Response(
            200, json=_listing({"id": "b", "title": "y"}, {"id": "c", "title": "z"})
        )
    )
    page = RedditCollector(access_token="t", subreddits=["sales", "consulting"]).collect("q")
    assert page.examined == 3


@respx.mock
def test_posts_are_requested_by_default_and_comments_on_request():
    route = respx.get(SALES_SEARCH).mock(return_value=httpx.Response(200, json=_listing()))

    RedditCollector(access_token="t", subreddits=["sales"]).collect("q")
    assert route.calls.last.request.url.params["type"] == "link"

    RedditCollector(access_token="t", subreddits=["sales"], include_comments=True).collect("q")
    assert route.calls.last.request.url.params["type"] == "comment"


@respx.mock
def test_a_comment_body_becomes_the_signal_text():
    """Comments carry text in `body`, not `selftext`; both must normalise."""
    respx.get(SALES_SEARCH).mock(
        return_value=httpx.Response(
            200,
            json=_listing(
                {
                    "id": "c1",
                    "body": "I freeze when the buyer pushes back",
                    "link_title": "How do you handle pushback?",
                    "subreddit": "sales",
                }
            ),
        )
    )
    page = RedditCollector(
        access_token="t", subreddits=["sales"], include_comments=True
    ).collect("pushback")

    signal = page.signals[0]
    assert signal.text == "I freeze when the buyer pushes back"
    assert signal.title == "How do you handle pushback?"


@respx.mock
@pytest.mark.parametrize(
    ("days_back", "expected"),
    [(0, "day"), (3, "week"), (20, "month"), (200, "year"), (900, "all")],
)
def test_the_window_picks_the_narrowest_reddit_time_bucket(days_back, expected):
    """Avoids paging through years of history only to discard it locally."""
    from datetime import datetime, timedelta, timezone

    route = respx.get(SALES_SEARCH).mock(return_value=httpx.Response(200, json=_listing()))
    RedditCollector(access_token="t", subreddits=["sales"]).collect(
        "q", since=datetime.now(timezone.utc) - timedelta(days=days_back)
    )
    assert route.calls.last.request.url.params["t"] == expected


@respx.mock
def test_no_time_bucket_is_sent_without_a_window():
    route = respx.get(SALES_SEARCH).mock(return_value=httpx.Response(200, json=_listing()))
    RedditCollector(access_token="t", subreddits=["sales"]).collect("q")
    assert "t" not in route.calls.last.request.url.params


@respx.mock
def test_missing_credentials_still_raise_with_subreddits_configured():
    with pytest.raises(RuntimeError, match="REDDIT_CLIENT_ID"):
        RedditCollector(subreddits=["sales"]).collect("q")
