import pytest

from praxis.core.filelock import try_lock


def test_a_second_holder_is_refused_and_can_read_who_holds_it(tmp_path):
    """Windows locks are mandatory, so a lock over the holder text would make the refusal unable
    to say who holds it. The lock sits past the text for that reason."""
    path = tmp_path / "jobs.lock"
    first = path.open("a+")
    second = path.open("a+")
    try:
        try_lock(first)
        first.seek(0)
        first.truncate()
        first.write("rebuild (pid 1)")
        first.flush()
        with pytest.raises(OSError):
            try_lock(second)
        second.seek(0)
        assert second.read() == "rebuild (pid 1)"
    finally:
        first.close()
        second.close()


def test_closing_the_handle_releases_the_lock(tmp_path):
    path = tmp_path / "run.lock"
    with path.open("a+") as first:
        try_lock(first)
    with path.open("a+") as second:
        try_lock(second)
