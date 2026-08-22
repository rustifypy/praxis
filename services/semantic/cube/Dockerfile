# Cube, with our `cube.js` baked in.
#
# It is here rather than bind-mounted because `cube.js` is portal-authored logic — it is the
# whole integration with the model store — and not user configuration. Mounting it meant the
# deployment could only ever be run from a directory that had this file in it, which is what
# stopped the hand-over being a single compose file.
#
# CUBE_BASE selects the upstream build. Most drivers want the default; the JDBC ones need `jdk`,
# which is why the portal picks the image tag from the driver spec rather than assuming one.
ARG CUBE_BASE=latest
FROM cubejs/cube:${CUBE_BASE}

COPY cube.js /cube/conf/cube.js
