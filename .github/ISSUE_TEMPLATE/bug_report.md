---
name: Bug report
about: Something in the agent, the gate, model generation, or the portal is wrong
title: ""
labels: bug
assignees: ""
---

**What happened**

A clear description of the bug.

**What you expected**

What should have happened instead.

**Steps to reproduce**

1. ...
2. ...

**For anything container- or model-dependent, include the actual evidence**

- The Cube JSON query, the SQL it compiled to, and the rejection text, if this is
  a query/gate bug (visible in the tool trace in the portal).
- `make logs SOURCE=<name>` output, if this is a source/container bug.
- Which step (`connect` / `cubes` / `describe` / `views`) and what it recorded, if
  this is a model-generation bug.

**Environment**

- OS:
- Docker version:
- Database/driver involved (if relevant):

**Relevant config**

Anything non-default in `.env` that could matter (redact secrets).
