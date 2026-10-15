"""The data model as something you work on: what is served, the branches, the files, the editor.

**The draft is the current model.** A branch being edited has a working copy on this machine, and
everything here reads that rather than the branch as committed. So a `PUT` to a file reaches no
deployment and compiles nothing; `check` compiles the draft on a throwaway ref; and `commit` *is*
the deploy, because Cube asks the store for the ref's revision on every request.

`commit` runs the gate a second time rather than trusting the verdict `check` gave. It has to: a
draft can be edited between the two, so what arrives at `commit` is not provably what was checked.

The editor's own chat lives here rather than in `chat/`. It is a different agent from the question
agent — pinned to one ref, reading proposals that are on no ref at all — and putting two unrelated
agents behind one product area would leave neither able to explain itself.
"""

from .routes import router

__all__ = ["router"]
