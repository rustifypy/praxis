# The analysis sandbox. Built on demand by `container.ensure_image`, tagged by this file's own
# digest — so editing it produces a new image rather than reusing a stale one under the old tag.
#
# Everything the agent can use has to be installed here, because the container runs with
# --network none and there is no `pip install` at run time. That is the point rather than a
# limitation: it is what makes a saved script reproduce, and what stops a CSV leaving the
# machine. Adding a library is an edit here and one rebuild.
#
# Versions are pinned for the same reason: an unpinned image silently changes what a script
# written last month does today.
FROM python:3.12-slim

RUN pip install --no-cache-dir \
      pandas==2.2.3 \
      polars==1.17.1 \
      duckdb==1.1.3 \
      pyarrow==18.1.0 \
      numpy==2.1.3 \
      scipy==1.14.1 \
      matplotlib==3.9.3 \
      openpyxl==3.1.5 \
      tabulate==0.9.0

# There is no display, and importing pyplot without this picks a GUI backend and fails
# somewhere a long way from the cause.
ENV MPLBACKEND=Agg
