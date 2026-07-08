import re


PULL_REQUEST_NUMBER_RE = re.compile(r"/pulls?/(\d+)(?:[/?#]|$)")


def extract_pull_request_number(url: str | None) -> int | None:
    if not url:
        return None

    match = PULL_REQUEST_NUMBER_RE.search(url)
    if not match:
        return None

    return int(match.group(1))
