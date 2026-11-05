"""A non-blocking exclusive lock on an open file, on POSIX and on Windows.

Both lock holders (`jobs.hold`, `flows.runner.hold`) write who holds the lock into the locked
file so a refused caller can say so. `flock` is advisory, so that read succeeds. Windows locks are
mandatory byte ranges, so the lock covers one byte far past anything written: the holder text stays
readable and the lock still excludes a second holder.
"""

import os

if os.name == "nt":
    import msvcrt

    # Windows allows locking past end-of-file; a lock here never overlaps the holder text.
    _OFFSET = 1 << 30

    def try_lock(handle) -> None:
        """Take the lock or raise `OSError`. Released when `handle` is closed."""
        position = handle.tell()
        handle.seek(_OFFSET)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        finally:
            handle.seek(position)

else:
    import fcntl

    def try_lock(handle) -> None:
        """Take the lock or raise `OSError`. Released when `handle` is closed."""
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
