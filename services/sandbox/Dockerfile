# The analysis sandbox, as a service.
#
# Everything the agent can use has to be installed here: there is no `pip install` at run time.
# That is the point rather than a limitation — it is what makes a saved script reproduce. Adding
# a library is an edit here and one rebuild.
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

WORKDIR /app
COPY pyproject.toml ./
COPY sandbox_service ./sandbox_service
RUN pip install --no-cache-dir .

ENV SANDBOX_ROOT=/workspaces \
    SANDBOX_PORT=8600
VOLUME /workspaces
EXPOSE 8600

HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.environ['SANDBOX_PORT']+'/health').read()"

CMD ["sandbox-service"]
