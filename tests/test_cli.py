import pytest

from relay_engine.cli import _parse_arguments
from relay_engine.feeds import FeedName


@pytest.mark.parametrize("feed", list(FeedName))
def test_pull_accepts_every_feed(feed):
    assert _parse_arguments(["pull", feed]).feed == feed


@pytest.mark.parametrize("feed", ["grasshopper_calls", "owner_notes"])
def test_pull_refuses_feeds_the_owner_uploads_by_hand(feed):
    with pytest.raises(SystemExit):
        _parse_arguments(["pull", feed])
