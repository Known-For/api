from app.lib.parse_linkedin import parse


SAMPLE = """Chuck Whitten
Co-CEO at Dell Technologies
2d
Excited to share our quarterly results — strong performance across the board.
This is the body of the post that has multiple sentences.
https://www.linkedin.com/posts/chuck-activity-1
123 reactions • 45 comments • 6 reposts

Chuck Whitten
Co-CEO at Dell Technologies
1w
Talked with the team about our infrastructure strategy.
We're investing in AI workloads heavily.
https://www.linkedin.com/posts/chuck-activity-2
89 reactions • 12 comments
"""


def test_parse_returns_two_posts():
    posts = parse(SAMPLE, author_name="Chuck Whitten")
    assert len(posts) == 2


def test_parse_extracts_engagement_counts():
    posts = parse(SAMPLE, author_name="Chuck Whitten")
    assert posts[0]["reactions"] == 123
    assert posts[0]["comments"] == 45
    assert posts[0]["reposts"] == 6
    assert posts[1]["reactions"] == 89
    assert posts[1]["comments"] == 12
    assert posts[1]["reposts"] == 0


def test_parse_extracts_url():
    posts = parse(SAMPLE, author_name="Chuck Whitten")
    assert posts[0]["url"] == "https://www.linkedin.com/posts/chuck-activity-1"
    assert posts[1]["url"] == "https://www.linkedin.com/posts/chuck-activity-2"


def test_parse_extracts_date_str():
    posts = parse(SAMPLE, author_name="Chuck Whitten")
    assert posts[0]["date_str"] == "2d"
    assert posts[1]["date_str"] == "1w"


def test_parse_handles_comma_separated_engagement():
    sample = "Some body\nhttps://l/x\n1,234 reactions • 12 comments"
    posts = parse(sample)
    assert len(posts) == 1
    assert posts[0]["reactions"] == 1234


def test_parse_skips_block_without_engagement():
    sample = "Just some random text with no engagement footer at all\n"
    posts = parse(sample)
    assert posts == []
