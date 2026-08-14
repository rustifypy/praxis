"""The sandbox's isolation contract, asserted without a daemon.

`argv` is the whole contract — the flags on that one command are all that stands between a
model-written shell command and the machine — so it is a pure function and this is where it
gets checked. What a command *does* once it runs is verified against a live container, not here.
"""

import hashlib
import os

import pytest

from analyst_agent.sandbox import container, scratch

CHAT = "c0ffee1234567890"


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    from analyst_agent.core.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    return tmp_path


def env_of(args: list[str]) -> dict[str, str]:
    pairs = [args[i + 1] for i, a in enumerate(args) if a == "-e"]
    return dict(p.partition("=")[::2] for p in pairs)


def mounts_of(args: list[str]) -> list[str]:
    return [args[i + 1] for i, a in enumerate(args) if a == "-v"]


# --- the one docker run -------------------------------------------------------


def test_the_sandbox_has_no_network_at_all():
    """Not an allowlist. There is nothing for a command to reach, so nothing it reads can
    leave and nothing can be installed at run time."""
    args = container.argv("/w")
    assert args[args.index("--network") + 1] == "none"


def test_exactly_one_directory_is_mounted_and_it_is_the_workspaces_root():
    """`analyst.db` is plaintext credentials and `.env` holds the Cube signing secret. Neither
    is protected by a deny list, because there is no list — there is one mount."""
    assert mounts_of(container.argv("/data/workspaces")) == ["/data/workspaces:/workspace"]


def test_argv_takes_no_mount_or_environment_from_its_caller():
    """The enforcement is the signature: a credential cannot reach the sandbox by being passed,
    only by editing `argv`. Same reason the dashboard tools have no layout parameter."""
    import inspect

    taken = set(inspect.signature(container.argv).parameters) - {"workspaces"}
    assert taken == {"image"}, f"argv grew a parameter: {taken}"


def test_the_only_environment_is_the_fixed_set():
    """A read-only root needs HOME and MPLCONFIGDIR pointed at the tmpfs. Nothing else, and
    nothing a caller chose."""
    assert env_of(container.argv("/w")) == container.FIXED_ENV


def test_the_container_cannot_write_outside_the_mount_or_gain_a_capability():
    args = container.argv("/w")
    assert "--read-only" in args
    assert args[args.index("--tmpfs") + 1].startswith("/tmp:rw")
    assert args[args.index("--cap-drop") + 1] == "ALL"
    assert args[args.index("--security-opt") + 1] == "no-new-privileges"


def test_memory_swap_equals_memory_so_the_limit_cannot_be_swapped_around():
    args = container.argv("/w")
    assert args[args.index("--memory") + 1] == container.MEMORY
    assert args[args.index("--memory-swap") + 1] == container.MEMORY


def test_a_fork_bomb_is_bounded_and_pid_one_can_reap():
    """`--pids-limit` is the bound; `--init` is what keeps a long-lived container from filling
    it with the zombies of exec'd processes that outlived their parent."""
    args = container.argv("/w")
    assert args[args.index("--pids-limit") + 1] == container.PIDS
    assert "--init" in args


def test_the_sandbox_does_not_come_back_after_a_reboot():
    """A source is `--restart unless-stopped` because nothing else starts one. The sandbox is
    the opposite: it holds no state, so a leftover is only ever built from stale settings."""
    assert "--restart" not in container.argv("/w")


def test_nothing_in_the_container_is_root_even_nominally():
    """The host's own uid, which is also what makes files it writes into the mount readable by
    the file tools."""
    args = container.argv("/w")
    assert args[args.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"


def test_the_image_tag_is_the_dockerfile_digest_so_an_edit_cannot_reuse_a_stale_build():
    digest = hashlib.sha256((container.IMAGE_DIR / "Dockerfile").read_bytes()).hexdigest()
    assert container.image_tag() == f"analyst-sandbox:{digest[:12]}"
    assert container.argv("/w")[-3] == container.image_tag()


def test_the_image_can_be_overridden_for_a_machine_that_builds_its_own(monkeypatch):
    monkeypatch.setenv(container.IMAGE_ENV, "my-sandbox:dev")
    assert container.image_tag() == "my-sandbox:dev"


# --- one command -------------------------------------------------------------


def test_a_command_runs_in_its_own_chats_directory():
    """One container serves every chat, so `--workdir` is what makes a relative path in a
    script mean this chat's files."""
    args = container.exec_argv(CHAT, "ls")
    assert args[args.index("--workdir") + 1] == f"/workspace/{CHAT}"


def test_the_timeout_is_inside_the_container_not_on_the_host():
    """Killing the `docker exec` client leaves the command running, so the kill has to come
    from the container's own init. TERM first, then KILL, so Python flushes what it printed."""
    args = container.exec_argv(CHAT, "sleep 999", timeout=30)
    assert args[args.index("timeout") + 1 : args.index("timeout") + 3] == ["--kill-after=5", "30"]
    # And the command is the last thing, unsplit — no shell on the host ever sees it.
    assert args[-3:] == ["bash", "-c", "sleep 999"]


# --- a path from the model ---------------------------------------------------


def test_a_path_inside_the_workspace_resolves():
    scratch.ensure(CHAT)
    target, refused = scratch.resolve(CHAT, "data/orders.csv")
    assert refused == ""
    assert target.name == "orders.csv"


@pytest.mark.parametrize("path", ["../other", "../../.env", "/etc/passwd", "a/../../b"])
def test_a_path_that_leaves_the_workspace_is_refused(path):
    scratch.ensure(CHAT)
    target, refused = scratch.resolve(CHAT, path)
    assert target is None
    assert "outside the workspace" in refused


def test_a_symlink_the_sandbox_planted_cannot_be_followed_out(data_dir):
    """`bash` can point a link at anything. The check is on the *resolved* target, which is why
    one check covers this as well as `..` — a host-side read would otherwise follow it."""
    root = scratch.ensure(CHAT)
    secret = data_dir / "analyst.db"
    secret.write_text("credentials")
    (root / "innocent.csv").symlink_to(secret)

    target, refused = scratch.resolve(CHAT, "innocent.csv")
    assert target is None
    assert "outside the workspace" in refused


def test_listing_hides_the_log_directory():
    """The log is somewhere to look when output was cut, not part of the work."""
    root = scratch.ensure(CHAT)
    (root / "orders.csv").write_text("a,b\n")
    (root / scratch.LOGS / "bash-001.log").write_text("noise")
    assert scratch.listing(CHAT) == ["orders.csv"]


def test_listing_a_missing_workspace_is_empty_rather_than_an_error():
    assert scratch.listing("never-used") == []


# --- files in and out --------------------------------------------------------


def test_an_upload_is_kept_apart_from_what_a_script_wrote(data_dir):
    """`uploads/` is the whole record that a file came from the user rather than from a run —
    there is no table saying so, and the directory is what the prompt reads."""
    scratch.save_upload(CHAT, "orders.csv", b"a,b\n")
    (scratch.ensure(CHAT) / "chart.png").write_bytes(b"png")

    assert scratch.uploaded(CHAT) == ["uploads/orders.csv"]
    assert sorted(scratch.listing(CHAT, "**/*")) == ["chart.png", "uploads/orders.csv"]


def test_a_snapshot_notices_a_file_rewritten_under_the_same_name(data_dir):
    """Size and mtime, not names: a script that re-draws `chart.png` adds no new name, and a set
    difference would report the run whose picture is on screen as having written nothing."""
    root = scratch.ensure(CHAT)
    (root / "chart.png").write_bytes(b"one")
    before = scratch.snapshot(CHAT)

    (root / "chart.png").write_bytes(b"two")
    os.utime(root / "chart.png", ns=(0, 0))
    after = scratch.snapshot(CHAT)

    assert [p for p, mark in after.items() if before.get(p) != mark] == ["chart.png"]


# --- what the model reads of a command's output ------------------------------


def test_short_output_is_handed_over_whole_and_writes_no_log():
    shown, log = scratch.record(CHAT, "bash", "42\n", limit=100)
    assert shown == "42\n"
    assert log == ""
    assert scratch.listing(CHAT, f"{scratch.LOGS}/*") == []


def test_long_output_is_trimmed_in_the_middle_and_kept_whole_in_a_log(data_dir):
    """The middle, not the head: a traceback keeps its cause at one end and its message at the
    other, so a head slice is the half that says least."""
    text = "".join(f"line {i}\n" for i in range(4000))
    shown, log = scratch.record(CHAT, "bash", text, limit=400)

    assert len(shown) < len(text)
    assert shown.startswith("line 0\n")
    assert shown.rstrip().endswith("line 3999")
    assert "characters trimmed" in shown
    # The path comes back relative, so `read_file` can open it.
    assert log == f"{scratch.LOGS}/bash-001.log"
    assert (data_dir / "workspaces" / CHAT / log).read_text() == text


def test_each_trimmed_command_gets_its_own_log():
    long = "x" * 5000
    assert scratch.record(CHAT, "bash", long, limit=100)[1].endswith("bash-001.log")
    assert scratch.record(CHAT, "bash", long, limit=100)[1].endswith("bash-002.log")
