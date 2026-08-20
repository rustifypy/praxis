"""Changing the Cube model on a branch, with a gate in front of the write.

The model directory is a git repo and the checked-out branch is what the container serves,
because the directory is a bind mount rather than something copied in. `git checkout` on the
host *is* the deploy. Depends on `..modelgen` for what a model is; nothing there depends on this.
"""
