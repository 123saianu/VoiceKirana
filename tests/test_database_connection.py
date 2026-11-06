
import pytest

from backend.database import DatabaseConnection


class FakeConnection:
    def __init__(self, fail_commit=False):
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self.close_calls = 0
        self.fail_commit = fail_commit

    def commit(self):
        if self.fail_commit:
            raise RuntimeError("Simulated commit failure")
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.close_calls += 1
        self.closed = True

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        return False


@pytest.mark.parametrize("is_postgres", [False, True])
def test_successful_transaction_commits_and_closes(is_postgres):
    raw_connection = FakeConnection()
    connection = DatabaseConnection(raw_connection, is_postgres)

    with connection:
        pass

    assert raw_connection.committed is True
    assert raw_connection.rolled_back is False
    assert raw_connection.closed is True
    assert raw_connection.close_calls == 1


@pytest.mark.parametrize("is_postgres", [False, True])
def test_failed_transaction_rolls_back_and_closes(is_postgres):
    raw_connection = FakeConnection()
    connection = DatabaseConnection(raw_connection, is_postgres)

    with pytest.raises(RuntimeError, match="Simulated database failure"):
        with connection:
            raise RuntimeError("Simulated database failure")

    assert raw_connection.committed is False
    assert raw_connection.rolled_back is True
    assert raw_connection.closed is True
    assert raw_connection.close_calls == 1


@pytest.mark.parametrize("is_postgres", [False, True])
def test_connection_closes_even_when_commit_fails(is_postgres):
    raw_connection = FakeConnection(fail_commit=True)
    connection = DatabaseConnection(raw_connection, is_postgres)

    with pytest.raises(RuntimeError, match="Simulated commit failure"):
        with connection:
            pass

    assert raw_connection.closed is True
    assert raw_connection.close_calls == 1


@pytest.mark.parametrize("is_postgres", [False, True])
def test_repeated_close_does_not_close_underlying_connection_twice(
    is_postgres,
):
    raw_connection = FakeConnection()
    connection = DatabaseConnection(raw_connection, is_postgres)

    connection.close()
    connection.close()

    assert raw_connection.closed is True
    assert raw_connection.close_calls == 1
@pytest.mark.parametrize("is_postgres", [True, False])
def test_nested_context_closes_only_after_outer_exit(is_postgres):
    fake = FakeConnection()
    connection = DatabaseConnection(fake, is_postgres)

    with connection:
        with connection:
            assert fake.closed is False

        # Exiting the inner context must not close the connection.
        assert fake.closed is False

    # The outer context must close it.
    assert fake.closed is True