from app.auth.rate_limit import LoginRateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_blocks_after_max_attempts_within_the_window():
    clock = FakeClock()
    limiter = LoginRateLimiter(max_attempts=3, window_seconds=60.0, clock=clock)
    for _ in range(3):
        assert not limiter.is_blocked("a@b.com")
        limiter.record_failure("a@b.com")
    assert limiter.is_blocked("a@b.com")


def test_different_keys_are_independent():
    clock = FakeClock()
    limiter = LoginRateLimiter(max_attempts=1, window_seconds=60.0, clock=clock)
    limiter.record_failure("a@b.com")
    assert limiter.is_blocked("a@b.com")
    assert not limiter.is_blocked("someone-else@b.com")


def test_old_attempts_fall_outside_the_window():
    clock = FakeClock()
    limiter = LoginRateLimiter(max_attempts=2, window_seconds=60.0, clock=clock)
    limiter.record_failure("a@b.com")
    clock.t += 61.0  # past the window
    limiter.record_failure("a@b.com")
    assert not limiter.is_blocked("a@b.com")  # only 1 attempt still counts


def test_reset_clears_the_block():
    clock = FakeClock()
    limiter = LoginRateLimiter(max_attempts=1, window_seconds=60.0, clock=clock)
    limiter.record_failure("a@b.com")
    assert limiter.is_blocked("a@b.com")
    limiter.reset("a@b.com")
    assert not limiter.is_blocked("a@b.com")
