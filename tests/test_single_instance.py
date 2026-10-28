from utils.single_instance import SingleInstanceLock


def test_second_lock_is_refused(tmp_path):
    path = tmp_path / "parser.lock"
    first = SingleInstanceLock(path)
    second = SingleInstanceLock(path)

    assert first.acquire()
    assert not second.acquire()

    first.release()
    assert second.acquire()
    second.release()
