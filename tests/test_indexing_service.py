from app.services.indexing_service import DocumentIndexingService


class FakeEmbeddings:
    def __init__(self, failures: list[Exception] | None = None):
        self.failures = failures or []
        self.calls: list[list[str]] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        if self.failures:
            raise self.failures.pop(0)
        return [[float(index)] for index, _ in enumerate(texts)]


class RateLimitedError(Exception):
    status_code = 429


def make_service(*, embeddings: FakeEmbeddings, **kwargs) -> DocumentIndexingService:
    return DocumentIndexingService(
        db=None,  # type: ignore[arg-type]
        embeddings=embeddings,
        repository=None,  # type: ignore[arg-type]
        token_counter=len,
        **kwargs,
    )


def test_embedding_texts_are_split_by_token_budget_in_input_order():
    embeddings = FakeEmbeddings()
    service = make_service(
        embeddings=embeddings,
        batch_token_budget=6,
        tokens_per_minute=60,
    )

    vectors = service._embed_texts(["abc", "de", "fghi", "j"])

    assert embeddings.calls == [["abc", "de"], ["fghi", "j"]]
    assert vectors == [[0.0], [1.0], [0.0], [1.0]]


def test_embedding_requests_wait_when_the_rolling_token_budget_is_full():
    embeddings = FakeEmbeddings()
    now = [0.0]
    waits: list[float] = []

    def sleeper(seconds: float) -> None:
        waits.append(seconds)
        now[0] += seconds

    service = make_service(
        embeddings=embeddings,
        batch_token_budget=5,
        tokens_per_minute=8,
        clock=lambda: now[0],
        sleeper=sleeper,
    )

    service._embed_texts(["aaaaa", "bbbbb"])

    assert waits == [60.0]
    assert embeddings.calls == [["aaaaa"], ["bbbbb"]]


def test_embedding_rate_limit_is_retried_with_backoff():
    embeddings = FakeEmbeddings(failures=[RateLimitedError("slow down")])
    waits: list[float] = []
    service = make_service(
        embeddings=embeddings,
        batch_token_budget=10,
        tokens_per_minute=100,
        max_retries=2,
        retry_base_delay_seconds=1.5,
        sleeper=waits.append,
    )

    vectors = service._embed_texts(["test"])

    assert vectors == [[0.0]]
    assert embeddings.calls == [["test"], ["test"]]
    assert waits == [1.5]
